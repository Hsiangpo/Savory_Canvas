<div align="center">
  <img src="./frontend/public/vite.svg" width="120" alt="Logo"/>
  <h1>🎨 Savory Canvas</h1>
  <p><b>一个由 Agent 驱动的智能图文创作工具</b><br/>从灵感对话到风格确认、多图生成，再到最终的图文并茂 PDF 导出，提供全自动的创作流体验。</p>

  <p>
    <img src="https://img.shields.io/badge/React-18-blue?logo=react&style=flat-square" alt="React" />
    <img src="https://img.shields.io/badge/TypeScript-5.0-blue?logo=typescript&style=flat-square" alt="TypeScript" />
    <img src="https://img.shields.io/badge/Vite-7.3-646CFF?logo=vite&style=flat-square" alt="Vite" />
    <img src="https://img.shields.io/badge/FastAPI-0.100+-009688?logo=fastapi&style=flat-square" alt="FastAPI" />
    <img src="https://img.shields.io/badge/SQLite-Local-003B57?logo=sqlite&style=flat-square" alt="SQLite" />
  </p>
</div>

---

## ✨ 核心特性

- 🤖 **Agent 智能对话流**：流式思维呈现（Thinking Bubble），清晰展现 Agent 的每一步工具调用（提取素材、总结等）与决策链。
- ⚡ **全自动执行管道**：支持 `style_collecting -> prompt_revision -> asset_confirming -> locked`。一旦用户确认创作方案并锁定，Agent 自动无缝拉起生成任务，无需人工干预。
- 🖼️ **强大的素材解析**：支持图片、视频、文本多模态素材上传与特征提取，自动梳理“美食、景点、关键词”资产。
- 🎨 **灵活的风格管理**：内置与自定义提示词风格模板，色彩情绪与绘画风格全局配置，本地存储支持反复重用。
- 📋 **高质感排版与导出**：一键生成分图提示词（`asset_allocate`），多并发生成图片与图文混排文案，支持一键导出精美的 PDF。

---

## 🛠️ 技术栈

| 领域 | 核心技术 | 简述 |
| :--- | :--- | :--- |
| **前端 (Frontend)** | React, TypeScript, Vite, Zustand | 采用响应式流式 UI 设计，支持实时动画与平滑滚动，全局状态由 Zustand 接管。 |
| **后端 (Backend)** | FastAPI, SQLite, Pytest | 异步高并发处理生成队列，持久化保存所有会话、资产及生成状态。 |
| **模型调度** | LLM Provider Abstraction | 支持灵活配置各家大模型 API，自动根据 `image_generation` 和 `text_generation` 标签进行路由。 |

---

## 🚀 快速启动

### 1️⃣ 启动后端 (Backend)

确保你已经在项目根目录下：

```bash
# 启动 FastAPI 服务
python -m uvicorn backend.app.main:app --app-dir . --host 127.0.0.1 --port 8887 --reload
```
> 💡 **提示**：在 PowerShell 环境下，请严格使用 `--app-dir .`（或绝对路径），避免使用 `$root` 导致解析错误。

### 2️⃣ 启动前端 (Frontend)

新开一个终端窗口，进入 `frontend` 目录：

```bash
cd frontend
npm install
npm run dev
```

启动后，访问本地环境进行创作：
- 🌐 **前端面板**：[http://localhost:7778](http://localhost:7778)
- 🔌 **API 接口**：[http://127.0.0.1:8887](http://127.0.0.1:8887)

---

## 🧪 测试与门禁

为了保证系统稳定性，在提交代码前请务必通过以下测试和检查。

### 🐍 后端测试 (Pytest)
```bash
cd backend
pytest -q
```

### ⚛️ 前端规范检查与构建
```bash
cd frontend
npm run lint && npm run build
```

---

## 📚 项目规范与契约

- **目录结构**：
  - `backend/`：核心 API 路由、Agent 工具链、生成 Workers 及单元测试。
  - `frontend/`：前端 UI 视图、网络请求（Axios + 流式 Fetch）及 Zustand 状态树。
  - `docs/`：迭代计划（Plans）、PRD 设计、流程架构说明。
- **API 契约**：以 `docs/OPENAPI.JSON` 为唯一真理（Single Source of Truth）。在调整接口实现前，请优先更新该文件。
- **鲁棒性设计**：
  - `/api/v1/models` 具备上游请求失败重试及本地缓存兜底机制，缓解模型提供商的网络波动。
  - 流式对话支持断线恢复，自动拉取 `getInspirationConversation` 兜底最新数据，拒绝重放污染脏数据。

---

## 💡 常见问题排查 (FAQ)

**Q1：浏览器控制台报 CORS 跨域错误？**  
👉 优先检查运行后端的控制台是否输出了 `500 Internal Server Error`。很多时候后端内部代码抛错在浏览器端会被掩盖并显示为 CORS 异常。

**Q2：模型列表偶发为空，无法选择模型？**  
👉 通常是上游的提供商 `/models` 接口临时无响应或超时。系统已有缓存逻辑兜底，但若是首次请求或缓存过期，建议检查 API Key 及科学上网环境。

**Q3：Agent 流式生成卡住，没有反应？**  
👉 检查网络连接情况。若是中途断流，系统会自动同步服务端最新状态并停止转圈。如有极端异常，可刷新页面，数据均会自动从 SQLite 恢复。
