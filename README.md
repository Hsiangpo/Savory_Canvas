# Savory Canvas

Savory Canvas 是一个“灵感对话 -> 风格确认 -> 多图生成 -> 图文文案 -> PDF 导出”的本地图文创作工具。

## 最新同步状态（2026-03-03）

- 灵感对话主流程：`style_collecting -> prompt_revision -> asset_confirming -> locked`
- 生成任务阶段：`asset_extract -> asset_allocate -> prompt_generate -> image_generate -> copy_generate -> finalize`
- 模型路由能力校验：
  - 图片模型必须包含 `image_generation`
  - 文本模型必须包含 `text_generation`
- `/api/v1/models` 已做上游失败重试与本地缓存兜底，降低上游波动导致的“暂无可用模型”概率
- 风格模板、样例图、会话与任务数据均持久化在本地 SQLite

## 技术栈

- 后端：FastAPI + SQLite + Pytest
- 前端：React + TypeScript + Vite + Zustand

## 目录结构

- `backend/`：后端 API、服务、仓储、任务执行、测试
- `frontend/`：前端页面、状态管理、接口调用
- `docs/`：PRD、OpenAPI、开发说明、迭代计划

## 本地启动

### 1) 启动后端（仓库根目录）

```bash
python -m uvicorn backend.app.main:app --app-dir . --host 127.0.0.1 --port 8887 --reload
```

说明：PowerShell 下不要使用 `--app-dir $root`，应使用 `--app-dir .` 或绝对路径。

### 2) 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端默认地址：`http://localhost:7778`  
后端默认地址：`http://127.0.0.1:8887`

## 测试与门禁

### 后端

```bash
cd backend
pytest -q
```

### 前端

```bash
cd frontend
npm run lint && npm run build
```

## API 契约

唯一契约基准：`docs/OPENAPI.JSON`。  
后端字段变更必须先更新该文件，再改代码。

## 常见问题

### 1) 浏览器提示 CORS

优先检查后端日志是否有 500。后端内部报错时，浏览器可能表现为 CORS。

### 2) 模型列表偶发空

通常是上游 `/models` 接口临时失败或超时。当前后端已做重试与缓存恢复，仍需保证上游提供商稳定可用。
