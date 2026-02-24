# BE-OPS-P1-001 CORS 联调故障修复计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复前端 `http://localhost:7778` 调用后端 `http://127.0.0.1:8887/api/v1/*` 的 CORS 拦截问题，确保模型设置页与会话页可正常联调。

**Architecture:** 问题由后端缺失 CORS 中间件导致，属于网关层横切配置缺陷，不是业务接口实现缺陷。修复方式是在 FastAPI 入口统一增加 `CORSMiddleware`，只放行前端开发源地址，保持接口路径、响应体和错误码契约不变。

**Tech Stack:** FastAPI, Starlette CORSMiddleware, pytest, PowerShell/Invoke-WebRequest

---

## 一、审计结论（已复现）

### 1. 现象
- 浏览器报错：`No 'Access-Control-Allow-Origin' header is present`
- 影响接口：
  - `GET /api/v1/sessions`
  - `GET /api/v1/providers`
  - `GET /api/v1/config/model-routing`

### 2. 证据
- `backend/app/main.py` 当前未注册 CORS 中间件。
- 运行时探测（Origin=`http://localhost:7778`）：
  - `GET /api/v1/sessions` 返回 `200`，但 `Access-Control-Allow-Origin` 为空。
  - `OPTIONS /api/v1/sessions` 返回 `405`，预检失败。
- `GET /openapi.json` 显示 `title=Savory Canvas API`，说明当前进程是正确后端，不是错服务。

### 3. 根因判定
- 根因在后端入口层：缺失跨域响应头与预检处理能力。
- 前端请求地址与业务路径正确，属于后端配置问题。

---

## 二、实施任务（按最小改动）

### Task 1: 增加后端 CORS 中间件

**Files:**
- Modify: `backend/app/main.py`

**Step 1: 写失败测试（先红）**
- 在 `backend/tests/` 新增或扩展 CORS 相关测试：
  - `OPTIONS /api/v1/sessions` 断言状态码不再是 `405`。
  - `GET /api/v1/sessions` 携带 Origin 后断言返回 `Access-Control-Allow-Origin=http://localhost:7778`。

**Step 2: 运行单测确认失败**
- Run: `pytest -q backend/tests -k cors`
- Expected: 至少 1 条失败（当前无中间件）。

**Step 3: 最小实现**
- 在 `create_app()` 中注册 `CORSMiddleware`：
  - `allow_origins=["http://localhost:7778","http://127.0.0.1:7778"]`
  - `allow_methods=["*"]`
  - `allow_headers=["*"]`
  - `allow_credentials=False`

**Step 4: 运行单测确认通过**
- Run: `pytest -q backend/tests -k cors`
- Expected: CORS 新增用例全部通过。

---

### Task 2: 联调回归验证（接口契约不变）

**Files:**
- Verify only (无代码改动)

**Step 1: 启动后端**
- Run: `python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8887 --reload`

**Step 2: 验证预检**
- Run:
  - `OPTIONS http://127.0.0.1:8887/api/v1/sessions`
  - Header:
    - `Origin: http://localhost:7778`
    - `Access-Control-Request-Method: GET`
- Expected:
  - 状态码 `200` 或 `204`
  - 返回 `Access-Control-Allow-Origin: http://localhost:7778`

**Step 3: 验证关键接口非 404 且具备 CORS 头**
- `GET /api/v1/sessions`
- `GET /api/v1/providers`
- `GET /api/v1/config/model-routing`
- Expected:
  - 不为 `404`
  - 响应头包含 `Access-Control-Allow-Origin`

**Step 4: 全量回归**
- Run: `pytest -q`
- Expected: 全绿。

---

## 三、风险与回滚

### 风险
- 若 `allow_origins` 配置过宽，开发阶段无感但存在安全扩散风险。
- 若 `allow_credentials=True` 且 `allow_origins=["*"]` 组合错误，会触发浏览器策略冲突。

### 回滚
- 回滚文件：`backend/app/main.py`
- 回滚动作：移除新增 `CORSMiddleware` 配置并重启服务。
- 回滚后影响：恢复当前故障状态（仅用于紧急排障对比）。

---

## 四、验收标准

- 浏览器不再出现 `providers/model-routing/sessions` 的 CORS 报错。
- 前端 `http://localhost:7778` 一次联调通过模型设置页和会话列表加载。
- `pytest -q` 全绿。
- API 契约无字段变更，`docs/OPENAPI.JSON` 无需调整。
