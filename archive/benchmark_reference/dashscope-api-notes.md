# 阿里云百炼 API 调用说明

## 一、三种协议对比

| 对比项 | OpenAI 兼容 | Anthropic 兼容 | DashScope 原生 |
|:---|:---|:---|:---|
| **Base URL** | `https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` | `https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic` | `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1` |
| **API Key** | `sk-sp-` 前缀 | `sk-sp-` 前缀 | `sk-ws-` 前缀 |
| **端点路径** | `/chat/completions` | `/v1/messages` | `/services/aigc/text-generation/generation` |
| **请求格式** | OpenAI Chat Completions | Anthropic Messages | DashScope 原生格式 |
| **响应格式** | OpenAI 标准格式 | Anthropic 标准格式 | DashScope 原生格式 |
| **联网搜索** | ✅ 支持 | ✅ 支持 | ✅ 支持 |
| **搜索来源返回** | ❌ 不支持 | ❌ 不支持 | ✅ 支持 |
| **角标标注** | ❌ 不支持 | ❌ 不支持 | ✅ 支持（需 `enable_citation`） |

---

## 二、模型兼容性

### 支持所有协议的模型
- `qwen3.7-max`
- `deepseek-v4-pro`
- `glm-5.2`

### 仅支持 OpenAI/Anthropic 兼容协议的模型
- `qwen3.8-max` ❌ DashScope 原生返回 400
- `qwen3.7-plus` ❌ DashScope 原生返回 400
- `qwen3.6-flash` ❌ DashScope 原生返回 400

### 不支持的模型类型
- 音频模型：`qwen-audio-3.0-tts-plus`、`qwen-audio-3.0-realtime-plus`
- 图像模型：`wan2.7-image`、`wan2.7-image-pro`

---

## 三、请求格式对比

### OpenAI 兼容

```json
POST /compatible-mode/v1/chat/completions

{
  "model": "qwen3.7-max",
  "messages": [
    { "role": "system", "content": "You are a helpful assistant." },
    { "role": "user", "content": "查询内容" }
  ],
  "enable_search": true,
  "search_options": {
    "search_strategy": "turbo"
  }
}
```

**响应格式：**
```json
{
  "id": "chatcmpl-xxx",
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "回答内容"
    }
  }],
  "usage": { "total_tokens": 100 }
}
```

### Anthropic 兼容

```json
POST /apps/anthropic/v1/messages

{
  "model": "qwen3.7-max",
  "max_tokens": 4096,
  "messages": [
    { "role": "user", "content": "查询内容" }
  ],
  "system": "You are a helpful assistant.",
  "enable_search": true,
  "search_options": {
    "search_strategy": "turbo"
  }
}
```

**响应格式：**
```json
{
  "content": [{
    "type": "text",
    "text": "回答内容"
  }],
  "usage": { "input_tokens": 50, "output_tokens": 50 }
}
```

### DashScope 原生

```json
POST /api/v1/services/aigc/text-generation/generation

{
  "model": "qwen3.7-max",
  "input": {
    "messages": [
      { "role": "system", "content": "You are a helpful assistant." },
      { "role": "user", "content": "查询内容" }
    ]
  },
  "parameters": {
    "result_format": "message",
    "enable_search": true,
    "search_options": {
      "search_strategy": "turbo",
      "enable_source": true
    }
  }
}
```

**响应格式：**
```json
{
  "output": {
    "choices": [{
      "message": {
        "role": "assistant",
        "content": "回答内容"
      }
    }],
    "search_info": {
      "search_results": [
        {
          "index": 1,
          "title": "网页标题",
          "url": "https://example.com",
          "site_name": "站点名称",
          "icon": "https://example.com/favicon.ico"
        }
      ]
    }
  },
  "usage": { "total_tokens": 100 }
}
```

---

## 四、联网搜索参数

### 通用参数（所有协议）

| 参数 | 类型 | 说明 |
|:---|:---|:---|
| `enable_search` | boolean | 是否启用联网搜索 |
| `search_strategy` | string | 搜索策略：`turbo`（默认）、`max`、`agent`、`agent_max` |

### DashScope 原生专属参数

| 参数 | 类型 | 说明 |
|:---|:---|:---|
| `enable_source` | boolean | 是否返回搜索来源列表 |
| `enable_citation` | boolean | 是否在回答中插入角标引用 |
| `citation_format` | string | 角标格式：`"[<number>]"`（默认）、`"[ref_<number>]"` |
| `forced_search` | boolean | 是否强制搜索（即使模型认为不需要） |
| `freshness` | number | 搜索时效性：7、30、180、365（天） |
| `assigned_site_list` | array | 限定搜索站点列表（最多 25 个） |

---

## 五、已知限制

### 1. 搜索来源返回限制
- **OpenAI 兼容**：即使设置 `enable_source: true` 也不返回来源
- **Anthropic 兼容**：即使设置 `enable_source: true` 也不返回来源
- **DashScope 原生**：正常返回 `output.search_info.search_results`

### 2. DashScope 原生模型限制
以下模型调用 DashScope 原生接口返回 400 错误：
- `qwen3.8-max`
- `qwen3.7-plus`
- `qwen3.6-flash`

错误信息：`"url error, please check url"`

**可能原因**：这些模型需要使用 `multimodal-generation` 端点而非 `text-generation`。

### 3. 多模态模型限制
- `qwen3.8-max` 是多模态模型，在 DashScope 原生协议下可能需要使用 `/services/aigc/multimodal-generation/generation` 端点
- 在 OpenAI/Anthropic 兼容协议下可正常使用

### 4. 响应时间
- 简单查询：1-3 秒
- 联网搜索：20-80 秒
- 建议超时设置：120 秒

---

## 六、API Key 说明

| Key 前缀 | 类型 | 可用协议 |
|:---|:---|:---|
| `sk-sp-` | Token Plan | OpenAI 兼容、Anthropic 兼容 |
| `sk-ws-` | 按量付费 | DashScope 原生、OpenAI 兼容、Anthropic 兼容 |

**注意**：
- Token Plan Key (`sk-sp-`) 只能访问 Token Plan 专属端点
- 按量付费 Key (`sk-ws-`) 可访问所有端点
- 两种 Key 不能混用

---

## 七、获取模型列表

```bash
GET /compatible-mode/v1/models

Authorization: Bearer sk-sp-***
```

**响应：**
```json
{
  "data": [
    { "id": "qwen3.8-max", "object": "model" },
    { "id": "qwen3.7-max", "object": "model" }
  ],
  "has_more": false
}
```

需要过滤掉非文本模型（包含 `audio`、`image`、`tts`、`realtime` 的模型 ID）。

---

*最后更新：2026-08-26*
