# 客户端落地配置

只在基准测试完成并取得精确模型 ID 后生成配置。使用目标客户端当前已有的配置结构，不猜测未知字段；密钥引用环境变量，不写入仓库。

## 通用环境变量

```powershell
$env:OPENAI_BASE_URL = "https://example.com/v1"
$env:OPENAI_API_KEY = "<secret>"
$env:OPENAI_MODEL = "<exact-model-id>"
```

## Python OpenAI SDK

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url=os.environ["OPENAI_BASE_URL"],
    api_key=os.environ["OPENAI_API_KEY"],
)

response = client.chat.completions.create(
    model=os.environ["OPENAI_MODEL"],
    messages=[{"role": "user", "content": "Hello"}],
)
```

## JavaScript OpenAI SDK

```javascript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: process.env.OPENAI_BASE_URL,
  apiKey: process.env.OPENAI_API_KEY,
});

const response = await client.chat.completions.create({
  model: process.env.OPENAI_MODEL,
  messages: [{ role: "user", content: "Hello" }],
});
```

配置后用测试集中的一个非敏感用例做烟雾测试。主备切换应由应用明确处理，不能通过把两个模型 ID 拼接到一个字段实现。
