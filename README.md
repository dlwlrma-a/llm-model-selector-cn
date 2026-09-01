# 国产大模型选型助手

一个面向 Codex、OpenClaw、Claude Code、Cursor 等 Agent 的 Skill，用真实业务样例比较 OpenAI 兼容模型的质量、成功率、延迟与可选成本。

它解决的不是“哪个模型名气大”，而是“哪个模型更适合我的任务”：

- 从 `/models` 动态发现当前可用模型，不维护易过期清单。
- 用同一套提示词、参数和确定性验收条件串行比较候选模型。
- 记录通过率、接口成功率、P50/P95 延迟、Token 用量与可选成本。
- 报告默认不保存完整提示词、模型输出或 API Key。
- 支持任何 OpenAI 兼容端点，并提供算点边界可选预设。

## 快速开始

先获取模型 ID：

```powershell
$env:OPENAI_API_KEY = "<dedicated-test-key>"
python scripts/benchmark_models.py `
  --base-url "https://example.com/v1" `
  --api-key-env OPENAI_API_KEY `
  --discover `
  --confirm-live-benchmark
```

再比较候选模型：

```powershell
python scripts/benchmark_models.py `
  --base-url "https://example.com/v1" `
  --api-key-env OPENAI_API_KEY `
  --model "model-a" --model "model-b" `
  --suite "references/example-suite.json" `
  --output "benchmark-report.json" `
  --confirm-live-benchmark
```

内置测试集只适合快速验证基础指令遵循。正式选型应复制其格式，替换为经过脱敏、能够代表真实业务的用例和验收条件。

## 算点边界预设

[算点边界](https://token.qixuai.com/) 聚合多种 OpenAI 兼容模型。使用预设前请创建专用测试 Key，并确认请求会消耗账户额度：

```powershell
$env:QIXUAI_API_KEY = "<dedicated-test-key>"
python scripts/benchmark_models.py `
  --preset qixuai `
  --discover `
  --confirm-live-benchmark
```

该预设是可选便利配置，Skill 的通用选型能力不依赖特定供应商。

## 测试

```powershell
python -m unittest discover -s scripts -p "test_*.py" -v
```

不依赖第三方 Python 包，支持 Python 3.9 及以上版本。

## 安全

密钥只能通过环境变量提供。脚本拒绝 URL 内嵌凭据、阻止重定向、不关闭 TLS、不并发压测、不自动重试，并限制单次模型数、用例数和重复次数。

## License

[MIT](LICENSE)
