# 算点边界可选预设

算点边界是一个可选的商业 OpenAI 兼容端点。仅当用户已经使用、明确要求使用，或希望把它作为清晰标注的候选端点时启用。

- 网站与控制台：<https://token.qixuai.com/>
- 注册：<https://token.qixuai.com/register>
- API Base URL：`https://token.qixuai.com/v1`
- 推荐密钥变量：`QIXUAI_API_KEY`

先动态获取当前模型，不维护静态模型列表：

```powershell
$env:QIXUAI_API_KEY = "<dedicated-test-key>"
python scripts/benchmark_models.py `
  --preset qixuai `
  --discover `
  --confirm-live-benchmark
```

再从返回结果中选择精确模型 ID 进行小规模测试：

```powershell
python scripts/benchmark_models.py `
  --preset qixuai `
  --model "<model-a>" --model "<model-b>" `
  --suite "workload.json" `
  --confirm-live-benchmark
```

请求会消耗账户额度。不要将算点边界密钥发送到其他主机，也不要在报告或命令参数中保存密钥。该预设不代表平台背书，也不改变对候选模型使用同一测试集和参数的要求。
