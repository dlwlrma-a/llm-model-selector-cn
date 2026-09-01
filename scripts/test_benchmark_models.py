import contextlib
import importlib.util
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).with_name("benchmark_models.py")
SPEC = importlib.util.spec_from_file_location("benchmark_models", SCRIPT_PATH)
benchmark = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(benchmark)


class FakeApiHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A003
        return

    def _json(self, status, payload, headers=None):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path == "/redirect/models":
            self.send_response(302)
            self.send_header("Location", "/v1/models")
            self.end_headers()
            return
        if self.path == "/v1/models":
            self._json(200, {"object": "list", "data": [{"id": "model-good"}, {"id": "model-bad"}]})
            return
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802
        content_length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(content_length))
        if self.headers.get("Authorization") != "Bearer unit-test-secret":
            self._json(401, {"error": {"message": "api_key=unit-test-secret"}})
            return
        model = body["model"]
        prompt = body["messages"][-1]["content"]
        expected = "EXPECTED" if "EXPECTED" in prompt else "UNKNOWN"
        content = expected if model == "model-good" else "WRONG"
        self._json(
            200,
            {
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )


class ServerFixture:
    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApiHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}/v1"
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class BenchmarkTests(unittest.TestCase):
    def test_normalize_rejects_embedded_credentials(self):
        with self.assertRaises(ValueError):
            benchmark.normalize_base_url("https://user:secret@example.com/v1")
        self.assertEqual(
            benchmark.normalize_base_url("https://example.com/v1/chat/completions"),
            "https://example.com/v1",
        )

    def test_validators(self):
        self.assertEqual(benchmark.validate_output(" yes ", {"type": "exact", "value": "yes"})[0], True)
        self.assertEqual(benchmark.validate_output("abc", {"type": "contains", "value": "b"})[0], True)
        self.assertEqual(benchmark.validate_output("ID-42", {"type": "regex", "value": r"^ID-\d+$"})[0], True)
        self.assertEqual(
            benchmark.validate_output("```json\n{\"ok\": true}\n```", {"type": "json_equals", "value": {"ok": True}})[0],
            True,
        )

    def test_confirmation_is_required_before_network(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = benchmark.main(["--base-url", "http://127.0.0.1:1/v1", "--discover", "--no-auth"])
        self.assertEqual(code, 2)
        self.assertIn("confirm-live-benchmark", stderr.getvalue())

    def test_redirect_is_not_followed(self):
        with ServerFixture() as fixture:
            redirect_base = fixture.base_url.replace("/v1", "/redirect")
            with self.assertRaises(RuntimeError) as ctx:
                benchmark.discover_models(redirect_base, None, 2)
        self.assertIn("HTTP 302", str(ctx.exception))

    def test_secret_redaction(self):
        text = benchmark.redact_text("Authorization: Bearer unit-test-secret api_key=sd-1234567890")
        self.assertNotIn("unit-test-secret", text)
        self.assertNotIn("sd-1234567890", text)
        self.assertIn("[REDACTED]", text)

        provider_error = benchmark.compact_error(
            {"error": {"message": "provider echoed unit-test-secret without a label"}},
            "unit-test-secret",
        )
        self.assertNotIn("unit-test-secret", provider_error)

    def test_end_to_end_ranking_and_report(self):
        suite = {
            "name": "unit suite",
            "system": "follow the format",
            "temperature": 0,
            "max_tokens": 16,
            "cases": [
                {
                    "id": "case-1",
                    "prompt": "Reply with EXPECTED",
                    "validator": {"type": "exact", "value": "EXPECTED"},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir, ServerFixture() as fixture:
            suite_path = Path(temp_dir) / "suite.json"
            output_path = Path(temp_dir) / "report.json"
            suite_path.write_text(json.dumps(suite), encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"TEST_API_KEY": "unit-test-secret"}), contextlib.redirect_stdout(stdout):
                code = benchmark.main(
                    [
                        "--base-url",
                        fixture.base_url,
                        "--api-key-env",
                        "TEST_API_KEY",
                        "--model",
                        "model-bad",
                        "--model",
                        "model-good",
                        "--suite",
                        str(suite_path),
                        "--output",
                        str(output_path),
                        "--confirm-live-benchmark",
                    ]
                )
            report_text = output_path.read_text(encoding="utf-8")
            report = json.loads(report_text)
        self.assertEqual(code, 0)
        self.assertEqual(report["ranking"][0]["model"], "model-good")
        self.assertEqual(report["ranking"][0]["pass_rate"], 1.0)
        self.assertEqual(report["ranking"][1]["pass_rate"], 0.0)
        self.assertNotIn("unit-test-secret", report_text)
        self.assertIn("推荐: model-good", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
