# 前后端联调问题（Round3）修复计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复模型设置与会话管理的 3 个联调问题，保证前端 `7778` 与后端 `8887` 一次联调通过。

**Architecture:** 当前问题是“前端能力缺口 + 后端能力缺口”叠加：模型面板未接 `/models` 导致用户自由输入无效模型名，触发后端 400；会话管理仅有创建与列表，缺少重命名/删除端到端能力。修复采用“前端先约束输入 + 后端补齐会话接口 + 契约同步”的最小闭环方案。

**Tech Stack:** React + TypeScript + Axios, FastAPI, SQLite, pytest

---

## 一、审计结论（已复现）

### 问题 1：模型下拉列表缺失（`/models` 未接入）

- 现象：模型设置页没有“模型下拉选择”，只能手输模型名。
- 证据：
  - 后端接口可用：`GET /api/v1/models?provider_id=...` 返回模型列表（含能力字段）。
  - 前端代码 `frontend/src/components/ModelPanel.tsx` 未调用 `getModels`，仅使用文本输入框。
- 影响：用户输入 `gpt-4o`、`mj-v6` 等不在后端白名单中的模型名，保存必然失败。

### 问题 2：会话管理缺少重命名/删除

- 现象：会话列表无可用重命名、删除功能。
- 证据：
  - 前端 `frontend/src/components/SessionPanel.tsx` 仅实现创建与切换。
  - 后端 `backend/app/api/v1/session.py` 仅有：
    - `POST /api/v1/sessions`
    - `GET /api/v1/sessions`
    - `GET /api/v1/sessions/{session_id}`
  - `docs/OPENAPI.JSON` 中 `/api/v1/sessions` 仅含 `post/get`，`/api/v1/sessions/{session_id}` 仅含 `get`。
- 影响：会话生命周期管理不完整，用户无法清理与维护会话。

### 问题 3：点击保存路由返回 400

- 现象：`POST /api/v1/config/model-routing` 返回 400。
- 证据：后端返回体为 `E-1006`，消息为“图片模型不存在”。
- 根因：前端发送了无效模型名（自由输入），未使用 `/models` 结果进行约束。

---

## 二、实施任务

### Task A（前端）：补齐 `/models` 接入与模型下拉约束

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/components/ModelPanel.tsx`

**Step 1: API 层补充模型列表方法**
- 新增 `ModelItem`、`ModelListResponse` 类型。
- 新增 `getModels(providerId: string)` 调用 `GET /api/v1/models?provider_id=...`。

**Step 2: ModelPanel 改为受限选择**
- 移除“模型名自由输入框”，改为下拉框。
- provider 变化时自动拉取模型列表。
- 分能力过滤：
  - `image_model` 仅显示 `capabilities` 包含 `image_generation` 的模型。
  - `text_model` 仅显示 `capabilities` 包含 `text_generation` 的模型。
- 默认选中第一项可用模型，避免空值提交。

**Step 3: 保存错误提示可见化**
- 保存失败时读取后端错误消息（例如 `E-1006` 中文 message），展示到 toast。
- 禁止仅提示“保存失败”。

**验收：**
- 模型设置页可看到模型下拉列表。
- 点击保存不再因“模型不存在”触发 400（在正常选择路径下）。

---

### Task B（后端）：补齐会话重命名/删除接口

**Files:**
- Modify: `backend/app/schemas/request.py`
- Modify: `backend/app/repositories/session_repo.py`
- Modify: `backend/app/services/session_service.py`
- Modify: `backend/app/api/v1/session.py`
- Modify: `backend/tests/test_session_flow.py`（或新增会话管理测试文件）
- Modify: `docs/OPENAPI.JSON`
- Modify: `docs/BACKEND.MD`
- Modify: `docs/FRONTED.MD`

**Step 1: 新增会话更新请求模型**
- `SessionUpdateRequest`（至少支持 `title` 更新）。

**Step 2: Repo/Service 补方法**
- `update_session_title(session_id, title)`。
- `delete_session(session_id)`。
- 404 仍用既有 not found 错误码语义。

**Step 3: API 层补路由**
- `PATCH /api/v1/sessions/{session_id}`
- `DELETE /api/v1/sessions/{session_id}`
- 返回结构与统一错误体保持一致。

**Step 4: 契约与文档同步**
- `docs/OPENAPI.JSON` 增补两个接口定义。
- `docs/BACKEND.MD`、`docs/FRONTED.MD` 更新会话管理能力描述与调用清单。

**验收：**
- 会话可重命名、可删除。
- 文档与代码契约一致。

---

### Task C（前端）：会话面板补重命名/删除交互

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/store.ts`
- Modify: `frontend/src/components/SessionPanel.tsx`

**Step 1: API 调用补齐**
- 新增 `updateSession(sessionId, {title})`。
- 新增 `deleteSession(sessionId)`。

**Step 2: store 补动作**
- 新增 `renameSession`、`removeSession`。
- 删除当前活动会话时，自动切换到剩余第一条或 `null`。

**Step 3: SessionPanel UI**
- 在更多菜单中新增“重命名”“删除”。
- 重命名弹窗与删除确认弹窗中文文案。
- 所有弹窗事件冒泡处理正确，避免误关闭。

**验收：**
- 用户可在会话列表直接重命名、删除。
- 状态更新与 UI 回显一致。

---

## 三、验证清单

### 后端
- `pytest -q` 全绿。
- 新增会话重命名/删除用例通过。
- 模型路由与模型列表相关用例保持通过。

### 前端
- 模型设置页：
  - 可选择 provider 与模型（下拉）。
  - 保存成功后无 400。
- 会话列表：
  - 可重命名、可删除、状态回显正确。

### 联调
- 前端 `http://localhost:7778`
- 后端 `http://127.0.0.1:8887`
- 模型设置、会话管理两个主流程一次通过。

---

## 四、风险与注意事项

- 会话删除会级联清理相关素材/任务/导出，前端需二次确认提示。
- 若后端暂未上线会话 PATCH/DELETE，前端应临时隐藏对应按钮，避免假交互。
- 模型列表接口当前返回固定白名单，若后续切换真实 provider 动态模型，需要同步扩展缓存与异常处理策略。

---

## 五、并行开发契约冻结（接口名 + 参数 + 响应）

> 该节作为本轮前后端并行开发唯一对齐基准，实施中不得各自扩展字段。

### 1. 模型列表（前端下拉数据源）

- 接口：`GET /api/v1/models`
- Query：
  - `provider_id`（必填，string）
- 200 响应：

```json
{
  "provider_id": "string",
  "items": [
    {
      "id": "gpt-image-1",
      "name": "gpt-image-1",
      "capabilities": ["image_generation"]
    },
    {
      "id": "gpt-4.1-mini",
      "name": "gpt-4.1-mini",
      "capabilities": ["text_generation"]
    }
  ]
}
```

- 错误：
  - `404`：提供商不存在（not found 错误结构）

### 2. 查询模型路由（模型设置初始化）

- 接口：`GET /api/v1/config/model-routing`
- Query：无
- 200 响应（两种）：
  - 未配置：`null`
  - 已配置：

```json
{
  "image_model": {
    "provider_id": "string",
    "model_name": "gpt-image-1"
  },
  "text_model": {
    "provider_id": "string",
    "model_name": "gpt-4.1-mini"
  },
  "updated_at": "2026-02-23T23:07:17.169124+08:00"
}
```

### 3. 保存模型路由

- 接口：`POST /api/v1/config/model-routing`
- Body（必填）：

```json
{
  "image_model": {
    "provider_id": "string",
    "model_name": "gpt-image-1"
  },
  "text_model": {
    "provider_id": "string",
    "model_name": "gpt-4.1-mini"
  }
}
```

- 200 响应：同“查询模型路由-已配置结构”
- 错误：
  - `400 + E-1006`：模型不存在/模型能力不匹配/提供商不可用
  - `422 + E-1099`：参数校验失败

### 4. 会话重命名（本轮新增）

- 接口：`PATCH /api/v1/sessions/{session_id}`
- Path：
  - `session_id`（必填，string）
- Body（必填）：

```json
{
  "title": "新的会话标题"
}
```

- 200 响应（Session）：

```json
{
  "id": "string",
  "title": "新的会话标题",
  "content_mode": "food",
  "created_at": "2026-02-23T00:00:00+08:00",
  "updated_at": "2026-02-23T00:00:00+08:00"
}
```

- 错误：
  - `404 + E-2001`：会话不存在
  - `422 + E-1099`：参数不合法

### 5. 会话删除（本轮新增）

- 接口：`DELETE /api/v1/sessions/{session_id}`
- Path：
  - `session_id`（必填，string）
- 200 响应：

```json
{
  "deleted": true
}
```

- 错误：
  - `404 + E-2001`：会话不存在

### 6. 前端实现约束（并行防乱套）

- `ModelPanel` 必须只提交来自 `/models` 的模型名，不允许自由文本提交。
- 若后端返回 `E-1006`，前端直接展示后端 `message`，不吞错。
- `SessionPanel` 的重命名/删除按钮在接口未就绪前可临时禁用，但不得提交未定义接口。
- 文档更新顺序固定：
  1. `docs/OPENAPI.JSON`
  2. `docs/BACKEND.MD`
  3. `docs/FRONTED.MD`
