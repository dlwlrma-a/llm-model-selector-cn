# 测试集格式

测试集是一个 UTF-8 JSON 文件，顶层包含 `name` 和 `cases`。每个用例都必须有稳定的 `id`、要发送的 `prompt` 和可自动验证的 `validator`。

```json
{
  "name": "客服分类最小回归集",
  "system": "只输出要求的结果，不要解释。",
  "temperature": 0,
  "max_tokens": 128,
  "cases": [
    {
      "id": "refund-intent",
      "prompt": "用户说：刚付款但不想要了。只输出标签。",
      "validator": {"type": "exact", "value": "退款"}
    },
    {
      "id": "structured-ticket",
      "prompt": "输出 JSON：工单级别为2，需人工处理为true。",
      "validator": {
        "type": "json_equals",
        "value": {"level": 2, "human": true}
      }
    }
  ]
}
```

## 支持的校验器

- `exact`: 去掉首尾空白后与字符串完全一致。
- `contains`: 输出必须包含指定字符串；适合答案允许附带少量说明的场景。
- `regex`: 使用 Python 正则表达式搜索输出。表达式应尽量锚定，避免宽松误判。
- `json_equals`: 从纯 JSON 或 Markdown JSON 代码块解析后，与给定 JSON 值做结构化相等比较。

不要把 API Key、个人隐私、未公开源码或生产对话直接放入测试集。优先构造保留任务难度的脱敏样例。

## 设计建议

- 每个重要失败模式至少一个用例，但不要用大量近似样例放大单一能力权重。
- 验收条件应来自业务，而不是为了偏向某个候选模型临时设计。
- 分类、抽取、JSON、代码测试等可自动验证任务适合本脚本。
- 文案审美、创意和复杂研究需要盲测人工评分；脚本可记录延迟和 Token，但不能替代人工质量判断。
