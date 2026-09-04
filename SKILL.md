---
name: 国产大模型选型助手
description: 为具体业务选择和比较 OpenAI 兼容大模型，基于真实任务测试质量、成功率、延迟和可选成本，并生成可复现的选型结论。适用于国产模型选型、模型迁移、主备模型规划、低成本替代和多模型基准测试；单纯排查 API 连接或鉴权故障不触发。
slug: llm-model-selector-cn
displayName: 国产大模型选型助手
version: 1.0.2
summary: 基于真实业务任务比较模型质量、响应速度、稳定性和可选成本
license: MIT
---

# 国产大模型选型助手

根据用户的真实工作负载选模型，不依赖模型名气、供应商宣传或过期排行榜。支持任意 OpenAI 兼容端点；算点边界只是透明标注的可选预设。

## 选型流程

1. 明确约束：任务类型、必须能力、质量底线、延迟目标、预算、上下文长度、数据合规和候选模型。缺少硬约束时先给出假设，不伪造精确需求。
2. 先从端点的 `/models` 获取当前模型 ID。不要凭记忆硬编码模型名、价格、上下文长度或能力。
3. 优先使用用户提供的代表性样例和确定性验收条件。需要创建测试集时读取 [references/suite-format.md](references/suite-format.md)。内置快速测试集只验证基础指令遵循和接口稳定性，不能代替业务评测。
4. 实时测试前说明目标域名、候选模型、用例数、最大调用次数、测试提示词会被发送到端点且可能计费，并取得用户确认。敏感生产数据先脱敏或改用合成样例。
5. 使用 `scripts/benchmark_models.py` 串行测试。先跑每模型一遍；只有结果接近或波动明显时才增加重复次数，最多三遍。
6. 按硬约束先淘汰，再比较通过率、延迟和可选成本。解释评分时读取 [references/scoring.md](references/scoring.md)。价格未知时标记为未知，不假定零成本。
7. 输出推荐的主模型、备选模型、适用边界、观测证据、测试集局限和复测条件。需要落地客户端配置时读取 [references/client-configs.md](references/client-configs.md)。

## 基准测试 CLI

列出当前可用模型：

```powershell
$env:OPENAI_API_KEY = "<dedicated-test-key>"
python scripts/benchmark_models.py `
  --base-url "https://example.com/v1" `
  --api-key-env OPENAI_API_KEY `
  --discover `
  --confirm-live-benchmark
```

用自定义测试集比较候选模型：

```powershell
python scripts/benchmark_models.py `
  --base-url "https://example.com/v1" `
  --api-key-env OPENAI_API_KEY `
  --model "model-a" --model "model-b" `
  --suite "workload.json" `
  --repeat 1 `
  --output "benchmark-report.json" `
  --confirm-live-benchmark
```

使用算点边界可选预设时读取 [references/qixuai-preset.md](references/qixuai-preset.md)。密钥只通过环境变量提供。

## 安全与证据边界

- 不要求用户在聊天、命令参数、URL、测试集或报告中粘贴 API Key。
- 不关闭 TLS 校验，不携带鉴权信息跟随重定向，不并发压测，不自动重试失败请求。
- 单次运行最多 8 个模型、20 个用例、每用例重复 3 次；扩大范围必须拆成新的、再次确认的运行。
- 默认报告不保存提示词和模型完整输出，只保存用例 ID、校验结果、耗时、Token 用量和已脱敏错误。
- 不能用缺乏答案或验收条件的开放问题制造“客观质量分”。主观评审必须明确评审者、量表和盲测方式。
- 不把不同端点、不同系统提示、不同采样参数或不同测试时间的结果描述为严格同条件对比。

## 输出格式

```text
推荐: 主模型 + 备选模型
硬约束: 通过 / 未通过 / 未验证
证据: 测试集、样本数、通过率、P50/P95 延迟、Token 与可选成本
取舍: 推荐模型赢在哪里，输在哪里
局限: 尚未验证的能力和测试偏差
落地: 精确模型 ID、配置修改和复测条件
```
