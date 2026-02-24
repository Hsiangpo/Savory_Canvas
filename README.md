# Savory Canvas

Savory Canvas 是一个“灵感对话 -> 风格锁定 -> 多图生成 -> 文案导出”的全栈应用。

## 技术栈

- 后端：FastAPI + SQLite + Pytest
- 前端：React + TypeScript + Vite + Zustand

## 目录结构

- `backend/`：后端 API、服务、仓储、测试
- `frontend/`：前端页面与状态管理
- `docs/`：PRD、OpenAPI、计划文档

## 本地启动

### 1) 启动后端

在仓库根目录执行：

```bash
python -m uvicorn backend.app.main:app --app-dir . --host 127.0.0.1 --port 8887 --reload
```

说明：PowerShell 下不要使用 `--app-dir $root`，应使用 `--app-dir .` 或绝对路径。

推荐使用自动清理旧进程脚本（Windows PowerShell）：

```powershell
.\scripts\start_backend.ps1
```

该脚本会先按端口自动停止旧后端进程，再启动新进程，避免新旧版本同时监听 `8887`。

### 2) 启动前端

```bash
cd frontend
npm install
npm run dev
```

默认前端地址：`http://localhost:7778`

## 测试与构建

### 后端测试

```bash
pytest -q
```

### 前端检查

```bash
cd frontend
npm run lint
npm run build
```

## API 契约

唯一契约基准：`docs/OPENAPI.JSON`

## 常见问题

### CORS 报错

如果浏览器提示 `No 'Access-Control-Allow-Origin' header`，先确认：

1. 后端是否按上面的命令启动成功。
2. 前端请求地址是否是 `http://127.0.0.1:8887/api/v1`。
3. 后端日志里是否出现 500（500 也会让浏览器表现为 CORS 错误）。

## 当前状态

- 前端：`npm run lint && npm run build` 通过
- 后端：`pytest -q` 通过
