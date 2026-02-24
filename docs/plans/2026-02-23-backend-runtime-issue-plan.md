# 后端运行实例错配修复计划（2026-02-23）

## 1. 问题定义

- 问题编号：`BE-OPS-P1-001`
- 问题描述：前端模型设置页请求 `GET /api/v1/providers` 与 `GET /api/v1/config/model-routing` 返回 `404`。
- 根因：`127.0.0.1:8000` 当前运行的不是 Savory Canvas 后端，而是其他服务实例（OpenAPI 标题为 `shein-spider-ui`）。

## 2. 复现与证据

- `GET http://127.0.0.1:8000/openapi.json` 返回 `title=shein-spider-ui`。
- `GET http://127.0.0.1:8000/api/v1/providers` 返回 `404`。
- `GET http://127.0.0.1:8000/api/v1/config/model-routing` 返回 `404`。
- 端口占用进程：`PID 25568`，命令行为 `python.exe run_server.py`。

## 3. 影响范围

- `ModelPanel` 无法加载提供商与模型路由配置。
- 新增提供商、保存路由均不可用。
- 影响前端核心联调路径，属于 `P1` 联调阻塞。

## 4. 修复动作

1. 停止占用 `8000` 的非目标服务进程（当前为 `PID 25568`）。
2. 在项目根目录启动 Savory Canvas 后端：
   - `python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload`
3. 若必须保留其他服务在 `8000`：
   - Savory Canvas 改用 `8001` 启动；
   - 前端 `baseURL` 改为 `http://127.0.0.1:8001/api/v1`。

## 5. 验收标准

1. `GET /openapi.json` 返回 `title=Savory Canvas API`。
2. `GET /api/v1/sessions` 返回非 `404`（可为 `200` 空列表）。
3. `GET /api/v1/providers` 返回非 `404`。
4. 前端模型设置面板打开后不再出现上述两个 `404` 报错。

