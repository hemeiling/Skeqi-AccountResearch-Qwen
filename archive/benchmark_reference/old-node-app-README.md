# 🔍 联网搜索API测试网站

基于 Node.js + Express 的接口测试小网站，用于测试联网搜索功能。默认配置为**北京区域**。

## ✨ 功能特性

- 🌐 发起搜索测试请求到搜索API（可配置）
- 📊 实时展示请求/响应信息（状态码、耗时、完整JSON）
- 📝 搜索结果摘要展示
- 🕐 测试历史记录（本地存储，保存最近10条）
- ⚙️ 可配置API端点、Key、超时等参数

## 🚀 快速开始

### 1. 安装依赖

```bash
npm install
```

### 2. 配置搜索API

编辑 `config.js` 文件，修改 `searchApi` 配置：

```javascript
searchApi: {
  baseUrl: "http://your-beijing-search-api.example.com/api/search", // 北京区域搜索API端点
  apiKey: "",  // 如有需要填写API Key
  customHeaders: {
    "Content-Type": "application/json",
  },
  defaultParams: {
    topK: 5,           // 返回结果数量
    timeout: 15000,    // 超时时间（毫秒）
    fetchFullText: false, // 是否抓取全文
  },
},
```

### 3. 启动服务

```bash
npm start
```

开发模式（自动重启）：
```bash
npm run dev
```

### 4. 访问网站

打开浏览器访问：**http://localhost:3000**

## 🛠 API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/config` | 获取配置信息（不含敏感密钥） |
| POST | `/api/search` | 发起搜索请求 |

### POST /api/search 请求体

```json
{
  "query": "搜索关键词",
  "topK": 5,
  "timeout": 15000,
  "fetchFullText": false,
  "customParams": {}
}
```

## 📁 项目结构

```
├── config.js          # 搜索API配置（北京区域）
├── server.js          # Express 服务器
├── searchService.js   # 搜索请求逻辑
├── package.json       # 项目依赖
├── public/
│   ├── index.html     # 前端页面
│   ├── css/style.css  # 样式
│   └── js/main.js     # 前端逻辑
└── README.md          # 说明文档
```

## ⚠️ 注意事项

- 搜索API的响应格式可能因供应商而异，`searchService.js` 中的 `extractSearchResults` 函数内置了多种常见格式的兼容（`results`、`data`、`items` 等），可根据你的API响应结构调整。
- 请勿在公开环境中暴露你的 `apiKey`。
