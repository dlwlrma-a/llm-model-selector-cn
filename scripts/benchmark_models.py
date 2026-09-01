#!/usr/bin/env python3
"""Small, reproducible benchmarks for OpenAI-compatible chat models."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import ssl
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib import error, parse, request


PRESETS = {
    "qixuai": {
        "base_url": "https://token.qixuai.com/v1",
        "api_key_env": "QIXUAI_API_KEY",
        "console": "https://token.qixuai.com/",
    }
}

MAX_MODELS = 8
MAX_CASES = 20
MAX_REPEAT = 3
MAX_RESPONSE_BYTES = 2_000_000
VALIDATOR_TYPES = {"exact", "contains", "regex", "json_equals"}
SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(api[-_ ]?key\s*[:=]\s*)[\"']?[^\s,;\"']+"),
    re.compile(r"\b(?:sk|sd)-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{8,}\b"),
)


class NoRedirectHandler(request.HTTPRedirectHandler):
    """Refuse redirects so credentials cannot move to another URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def redact_text(value: str) -> str:
    result = value
    for pattern in SECRET_PATTERNS:
        if pattern.groups:
            result = pattern.sub(r"\1[REDACTED]", result)
        else:
            result = pattern.sub("[REDACTED]", result)
    return result


def normalize_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    parsed = parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an absolute http:// or https:// URL")
    if parsed.username or parsed.password:
        raise ValueError("credentials are not allowed in the base URL")
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/models"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return parse.urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", "")).rstrip("/")


def endpoint(base_url: str, resource: str) -> str:
    return f"{base_url.rstrip('/')}/{resource.lstrip('/')}"


def read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"无法读取 {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 无效 {path}: {exc}") from exc


def load_suite(path: Path) -> Dict[str, Any]:
    suite = read_json_file(path)
    if not isinstance(suite, dict):
        raise ValueError("测试集顶层必须是 JSON object")
    name = suite.get("name")
    cases = suite.get("cases")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("测试集 name 必须是非空字符串")
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise ValueError(f"测试集 cases 数量必须为 1-{MAX_CASES}")
    if not isinstance(suite.get("system", ""), str):
        raise ValueError("测试集 system 必须是字符串")
    temperature = suite.get("temperature", 0)
    max_tokens = suite.get("max_tokens", 256)
    if not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
        raise ValueError("temperature 必须是 0-2 的数字")
    if not isinstance(max_tokens, int) or not 1 <= max_tokens <= 4096:
        raise ValueError("max_tokens 必须是 1-4096 的整数")

    seen = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"cases[{index}] 必须是 object")
        case_id = case.get("id")
        prompt = case.get("prompt")
        validator = case.get("validator")
        if not isinstance(case_id, str) or not case_id.strip() or len(case_id) > 80:
            raise ValueError(f"cases[{index}].id 必须是 1-80 字符的字符串")
        if case_id in seen:
            raise ValueError(f"用例 id 重复: {case_id}")
        seen.add(case_id)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"用例 {case_id} 的 prompt 必须是非空字符串")
        if not isinstance(validator, dict) or validator.get("type") not in VALIDATOR_TYPES:
            raise ValueError(f"用例 {case_id} 的 validator.type 无效")
        if "value" not in validator:
            raise ValueError(f"用例 {case_id} 的 validator 缺少 value")
        if validator["type"] in {"exact", "contains", "regex"} and not isinstance(
            validator["value"], str
        ):
            raise ValueError(f"用例 {case_id} 的 {validator['type']} value 必须是字符串")
        if validator["type"] == "regex":
            try:
                re.compile(validator["value"])
            except re.error as exc:
                raise ValueError(f"用例 {case_id} 的 regex 无效: {exc}") from exc
    return suite


def load_prices(path: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    if path is None:
        return {}
    data = read_json_file(path)
    if not isinstance(data, dict):
        raise ValueError("价格表顶层必须是 object")
    for model, price in data.items():
        if not isinstance(model, str) or not isinstance(price, dict):
            raise ValueError("价格表必须把模型 ID 映射到价格 object")
        for field in ("input_per_million", "output_per_million"):
            value = price.get(field)
            if not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{model}.{field} 必须是非负数字")
        if not isinstance(price.get("currency"), str) or not price["currency"].strip():
            raise ValueError(f"{model}.currency 必须是非空字符串")
    return data


def parse_json_output(text: str) -> Any:
    candidate = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    return json.loads(candidate)


def validate_output(text: str, validator: Dict[str, Any]) -> Tuple[bool, str]:
    kind = validator["type"]
    expected = validator["value"]
    if kind == "exact":
        passed = text.strip() == expected
        return passed, "exact match" if passed else "exact mismatch"
    if kind == "contains":
        passed = expected in text
        return passed, "substring found" if passed else "substring missing"
    if kind == "regex":
        passed = re.search(expected, text) is not None
        return passed, "regex matched" if passed else "regex did not match"
    try:
        actual = parse_json_output(text)
    except (json.JSONDecodeError, TypeError):
        return False, "output is not valid JSON"
    passed = actual == expected
    return passed, "JSON equal" if passed else "JSON value mismatch"


def build_opener() -> request.OpenerDirector:
    return request.build_opener(NoRedirectHandler())


def http_json(
    url: str,
    api_key: Optional[str],
    timeout: float,
    body: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Any, int]:
    headers = {"Accept": "application/json", "User-Agent": "llm-model-selector-cn/1.0"}
    data = None
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    started = time.monotonic()
    try:
        with build_opener().open(req, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            status = response.status
    except error.HTTPError as exc:
        raw = exc.read(MAX_RESPONSE_BYTES + 1)
        status = exc.code
    elapsed_ms = round((time.monotonic() - started) * 1000)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("response exceeds 2 MB safety limit")
    text = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {"_raw_error": redact_text(re.sub(r"\s+", " ", text).strip())[:500]}
    return status, payload, elapsed_ms


def redact_secret(value: str, secret: Optional[str]) -> str:
    result = redact_text(value)
    if secret:
        result = result.replace(secret, "[REDACTED]")
    return result


def compact_error(payload: Any, secret: Optional[str] = None) -> str:
    if isinstance(payload, dict):
        nested = payload.get("error")
        if isinstance(nested, dict):
            for key in ("message", "detail", "code", "type"):
                if nested.get(key):
                    return redact_secret(str(nested[key]), secret)[:500]
        for key in ("message", "detail", "_raw_error"):
            if payload.get(key):
                return redact_secret(str(payload[key]), secret)[:500]
    return "API returned an unusable response"


def network_error(exc: BaseException) -> str:
    reason = exc.reason if isinstance(exc, error.URLError) else exc
    if isinstance(reason, socket.gaierror):
        return "DNS resolution failed"
    if isinstance(reason, ssl.SSLError):
        return "TLS validation or handshake failed"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "request timed out"
    if isinstance(reason, ConnectionRefusedError):
        return "connection refused"
    return redact_text(str(reason))[:500]


def discover_models(base_url: str, api_key: Optional[str], timeout: float) -> List[str]:
    status, payload, _ = http_json(endpoint(base_url, "models"), api_key, timeout)
    if not 200 <= status < 300:
        raise RuntimeError(f"GET /models 返回 HTTP {status}: {compact_error(payload, api_key)}")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise RuntimeError("GET /models 响应缺少 data 数组")
    return sorted(
        {str(item["id"]) for item in data if isinstance(item, dict) and item.get("id")}
    )


def usage_from_payload(payload: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    return (
        input_tokens if isinstance(input_tokens, int) and input_tokens >= 0 else None,
        output_tokens if isinstance(output_tokens, int) and output_tokens >= 0 else None,
    )


def extract_content(payload: Dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ValueError("response does not contain choices[0].message.content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}:
                parts.append(str(item.get("text", "")))
        if parts:
            return "".join(parts)
    raise ValueError("message.content is not text")


def run_case(
    base_url: str,
    api_key: Optional[str],
    timeout: float,
    model: str,
    suite: Dict[str, Any],
    case: Dict[str, Any],
    repeat_index: int,
) -> Dict[str, Any]:
    messages = []
    if suite.get("system"):
        messages.append({"role": "system", "content": suite["system"]})
    messages.append({"role": "user", "content": case["prompt"]})
    body = {
        "model": model,
        "messages": messages,
        "temperature": suite.get("temperature", 0),
        "max_tokens": suite.get("max_tokens", 256),
        "stream": False,
    }
    result: Dict[str, Any] = {
        "case_id": case["id"],
        "repeat": repeat_index,
        "success": False,
        "passed": False,
        "http_status": None,
        "latency_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "output_chars": None,
        "validation": "not run",
        "error": None,
    }
    try:
        status, payload, elapsed = http_json(
            endpoint(base_url, "chat/completions"), api_key, timeout, body
        )
        result["http_status"] = status
        result["latency_ms"] = elapsed
        if not 200 <= status < 300:
            result["error"] = compact_error(payload, api_key)
            return result
        if not isinstance(payload, dict):
            result["error"] = "response body is not a JSON object"
            return result
        content = extract_content(payload)
        input_tokens, output_tokens = usage_from_payload(payload)
        passed, validation = validate_output(content, case["validator"])
        result.update(
            {
                "success": True,
                "passed": passed,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "output_chars": len(content),
                "validation": validation,
            }
        )
    except (error.URLError, OSError) as exc:
        result["error"] = network_error(exc)
    except ValueError as exc:
        result["error"] = redact_text(str(exc))[:500]
    return result


def percentile(values: List[int], fraction: float) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def summarize_model(
    model: str, results: List[Dict[str, Any]], price: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    total = len(results)
    success_count = sum(1 for item in results if item["success"])
    pass_count = sum(1 for item in results if item["passed"])
    latencies = [item["latency_ms"] for item in results if isinstance(item["latency_ms"], int)]
    input_values = [item["input_tokens"] for item in results]
    output_values = [item["output_tokens"] for item in results]
    usage_complete = all(isinstance(value, int) for value in input_values + output_values)
    total_input = sum(input_values) if usage_complete else None
    total_output = sum(output_values) if usage_complete else None
    estimated_cost = None
    currency = None
    if price is not None and total_input is not None and total_output is not None:
        estimated_cost = round(
            total_input * price["input_per_million"] / 1_000_000
            + total_output * price["output_per_million"] / 1_000_000,
            8,
        )
        currency = price["currency"]
    return {
        "model": model,
        "calls": total,
        "success_count": success_count,
        "success_rate": round(success_count / total, 4),
        "pass_count": pass_count,
        "pass_rate": round(pass_count / total, 4),
        "latency_p50_ms": round(statistics.median(latencies)) if latencies else None,
        "latency_p95_ms": percentile(latencies, 0.95),
        "input_tokens": total_input,
        "output_tokens": total_output,
        "estimated_cost": estimated_cost,
        "currency": currency,
        "all_cases_passed": pass_count == total,
    }


def rank_summaries(summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        summaries,
        key=lambda item: (
            -item["pass_rate"],
            -item["success_rate"],
            item["latency_p50_ms"] if item["latency_p50_ms"] is not None else math.inf,
            item["model"],
        ),
    )


def render_text(report: Dict[str, Any]) -> str:
    lines = [
        "国产大模型选型助手",
        f"测试集: {report['suite']['name']}",
        f"端点: {report['base_url']}",
        f"每模型调用: {report['suite']['case_count'] * report['repeat']}",
        "",
    ]
    passing = [item for item in report["ranking"] if item["all_cases_passed"]]
    lines.append(f"推荐: {passing[0]['model']}" if passing else "推荐: 无模型全部通过，请先修订候选或验收条件")
    for index, item in enumerate(report["ranking"], 1):
        cost = "unknown"
        if item["estimated_cost"] is not None:
            cost = f"{item['estimated_cost']:.8f} {item['currency']}"
        latency_p50 = item["latency_p50_ms"] if item["latency_p50_ms"] is not None else "unknown"
        latency_p95 = item["latency_p95_ms"] if item["latency_p95_ms"] is not None else "unknown"
        lines.extend(
            [
                "",
                f"{index}. {item['model']}",
                f"   通过率: {item['pass_count']}/{item['calls']} ({item['pass_rate']:.1%})",
                f"   成功率: {item['success_count']}/{item['calls']} ({item['success_rate']:.1%})",
                f"   延迟: P50 {latency_p50} ms; P95 {latency_p95} ms",
                f"   Token: input={item['input_tokens']}; output={item['output_tokens']}",
                f"   估算成本: {cost}",
            ]
        )
    lines.extend(
        [
            "",
            "排序规则: 通过率降序、成功率降序、P50 延迟升序。",
            "局限: 自动校验只覆盖测试集定义的能力；小样本延迟不是稳定 SLA。",
        ]
    )
    return "\n".join(lines)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    default_suite = Path(__file__).resolve().parent.parent / "references" / "example-suite.json"
    parser = argparse.ArgumentParser(description="比较 OpenAI 兼容聊天模型的质量、延迟和可选成本。")
    parser.add_argument("--base-url", help="API Base URL，通常以 /v1 结尾")
    parser.add_argument("--preset", choices=sorted(PRESETS), help="透明声明的可选端点预设")
    parser.add_argument("--api-key-env", help="保存 API Key 的环境变量名")
    parser.add_argument("--no-auth", action="store_true", help="不发送 Authorization 请求头")
    parser.add_argument("--discover", action="store_true", help="只调用 /models 并输出模型 ID")
    parser.add_argument("--model", action="append", help="候选模型 ID；可重复，最多 8 个")
    parser.add_argument("--suite", type=Path, default=default_suite, help="JSON 测试集路径")
    parser.add_argument("--prices", type=Path, help="可选 JSON 价格表")
    parser.add_argument("--repeat", type=int, default=1, help="每用例重复次数，1-3")
    parser.add_argument("--timeout", type=float, default=60.0, help="单请求超时秒数，1-120")
    parser.add_argument("--output", type=Path, help="可选 JSON 报告输出路径")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--confirm-live-benchmark",
        action="store_true",
        help="确认端点会收到 Key 与测试提示词，并且请求可能计费",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if not args.confirm_live_benchmark:
        print(
            "缺少 --confirm-live-benchmark，已拒绝实时请求。请先确认目标端点、候选模型、调用次数、测试数据和可能费用。",
            file=sys.stderr,
        )
        return 2
    if not 1 <= args.repeat <= MAX_REPEAT:
        print(f"--repeat 必须为 1-{MAX_REPEAT}", file=sys.stderr)
        return 2
    if not 1 <= args.timeout <= 120:
        print("--timeout 必须为 1-120 秒", file=sys.stderr)
        return 2

    preset = PRESETS.get(args.preset or "", {})
    raw_base_url = args.base_url or preset.get("base_url")
    if not raw_base_url:
        print("请提供 --base-url 或 --preset", file=sys.stderr)
        return 2
    try:
        base_url = normalize_base_url(raw_base_url)
    except ValueError as exc:
        print(f"Base URL 无效: {exc}", file=sys.stderr)
        return 2

    api_key: Optional[str] = None
    key_env = args.api_key_env or preset.get("api_key_env") or "OPENAI_API_KEY"
    if not args.no_auth:
        api_key = os.environ.get(key_env)
        if not api_key or not api_key.strip():
            print(f"API Key 环境变量缺失或为空: {key_env}", file=sys.stderr)
            return 2
        api_key = api_key.strip()

    if args.discover:
        try:
            models = discover_models(base_url, api_key, args.timeout)
        except (RuntimeError, ValueError, error.URLError, OSError) as exc:
            print(f"模型发现失败: {redact_text(str(exc))}", file=sys.stderr)
            return 1
        payload = {"base_url": base_url, "models": models, "count": len(models)}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.format == "json" else "\n".join(models))
        return 0

    models = list(dict.fromkeys(args.model or []))
    if not models:
        print("基准测试至少需要一个 --model；可先用 --discover 获取模型 ID", file=sys.stderr)
        return 2
    if len(models) > MAX_MODELS:
        print(f"单次最多测试 {MAX_MODELS} 个模型", file=sys.stderr)
        return 2
    if any(not model.strip() or len(model) > 200 or any(ord(ch) < 32 for ch in model) for model in models):
        print("模型 ID 包含空值、控制字符或长度超过 200", file=sys.stderr)
        return 2

    try:
        suite = load_suite(args.suite)
        prices = load_prices(args.prices)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    all_results: Dict[str, List[Dict[str, Any]]] = {model: [] for model in models}
    for model in models:
        for repeat_index in range(1, args.repeat + 1):
            for case in suite["cases"]:
                all_results[model].append(
                    run_case(base_url, api_key, args.timeout, model, suite, case, repeat_index)
                )

    summaries = [summarize_model(model, all_results[model], prices.get(model)) for model in models]
    ranking = rank_summaries(summaries)
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "authentication": "none" if args.no_auth else f"environment:{key_env}",
        "preset": args.preset,
        "suite": {
            "name": suite["name"],
            "case_count": len(suite["cases"]),
            "temperature": suite.get("temperature", 0),
            "max_tokens": suite.get("max_tokens", 256),
        },
        "repeat": args.repeat,
        "ranking_rule": ["pass_rate_desc", "success_rate_desc", "latency_p50_asc"],
        "ranking": ranking,
        "results": all_results,
        "limitations": [
            "完整提示词和模型输出未写入报告。",
            "自动质量判断仅覆盖测试集定义的验收条件。",
            "小样本延迟不能作为稳定 SLA。",
            "价格未知不等于零成本。",
        ],
    }
    rendered_json = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered_json + "\n", encoding="utf-8")
    print(rendered_json if args.format == "json" else render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
