# 灵感对话一体化改造 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将“素材区 + 风格配置对话”合并为“灵感对话”单面板，支持文本/图片/视频混合输入并完成风格收敛到生成链路闭环。  
**Architecture:** 以后端“会话化灵感状态机 + 持久化消息流”为核心，前端改为单聊天面板驱动。旧 `styles/chat` 仅保留兼容，不作为主入口。  
**Tech Stack:** FastAPI + SQLite + React + TypeScript + Axios。

---

## 1. 范围与强约束

1. 本轮只做“灵感对话一体化”主链路，不新增无关功能。
2. 所有接口契约以 `docs/OPENAPI.JSON` 为唯一基准。
3. 用户可见文案必须中文。
4. P1 必须先于 P2。
5. 后端实现思想对齐 `E:\Develop\Masterpiece\Agent\Novel_Agent` 的“状态机 + 结构化输出 + 校验降级”方法论，但禁止跨项目直接拷贝代码。

## 2. 契约冻结（新增接口）

### 2.1 新增接口

1. `GET /api/v1/inspirations/{session_id}`
2. `POST /api/v1/inspirations/messages`（`multipart/form-data`）

### 2.2 `POST /api/v1/inspirations/messages` 请求字段

1. `session_id: string`（必填）
2. `text: string`（可选）
3. `selected_items: string[]`（可选）
4. `action: "continue" | "confirm_prompt" | "save_style" | "skip_save"`（可选）
5. `images: binary[]`（可选）
6. `videos: binary[]`（可选）

### 2.3 响应结构（两接口统一）

1. `session_id: string`
2. `messages: InspirationMessage[]`
3. `draft: InspirationDraft`

### 2.4 本轮契约补充点

1. `AssetTextCreateRequest.asset_type` 增加 `scenic_name`。
2. `Asset.asset_type` 增加 `scenic_name`、`image`。
3. `ModelInfo.capabilities` 增加 `vision`。
4. 图片输入且 `text_model` 无 `vision` 时返回 `400 + E-1010`。

## 3. 并行任务分配

| 优先级 | Owner | 模块 | 交付物 |
| --- | --- | --- | --- |
| P1 | Backend | 灵感状态机与接口 | inspirations 两接口 + 持久化 + 测试 |
| P1 | Frontend | 灵感面板替换 | 单面板聊天 UI + 混合输入 + 单选直发 |
| P1 | Frontend/Backend | 联调与回归 | 关键路径冒烟通过 |
| P2 | Backend | 生成链路语义增强 | `content_mode` 分支质量提升 |
| P2 | Frontend | 体验优化 | 降级可视化、附件交互细节 |

## 4. 后端任务清单（Owner: Backend）

### Task B1（P1）：接口与容器接线

**Files:**
- Modify: `backend/app/api/router.py`
- Modify: `backend/app/api/deps.py`
- Create: `backend/app/api/v1/inspiration.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/container.py`

**Step 1:** 新增 inspirations 路由注册。  
**Step 2:** 暴露 `InspirationService` 依赖注入。  
**Step 3:** 两接口按 OpenAPI 返回 `InspirationConversationResponse`。  
**Step 4:** 增加路由级参数校验与统一错误体。  
**Step 5:** 运行 API 冒烟测试并提交。

### Task B2（P1）：状态持久化与消息流

**Files:**
- Create/Modify: `backend/app/repositories/inspiration_repo.py`
- Modify: `backend/app/services/inspiration_service.py`
- Modify: `backend/migrations/001_init.sql`

**Step 1:** 建立 `inspiration_state`、`inspiration_message` 存储。  
**Step 2:** 首包自动初始化欢迎语。  
**Step 3:** 接收文本/图片/视频混合输入并入消息流。  
**Step 4:** 视频转写完成后并入上下文。  
**Step 5:** 锁定草案后禁止再次修改并返回明确提示。

### Task B3（P1）：模型能力校验与错误语义

**Files:**
- Modify: `backend/app/services/inspiration_service.py`
- Modify: `backend/app/services/model_service.py`
- Modify: `backend/app/schemas/response.py`（如需能力枚举扩展）

**Step 1:** 路由校验 `image_model` 具备 `image_generation`。  
**Step 2:** 路由校验 `text_model` 具备 `text_generation`。  
**Step 3:** 图片输入场景额外校验 `text_model` 含 `vision`。  
**Step 4:** 不满足时返回 `E-1010`，HTTP 400，错误体结构不变。  
**Step 5:** 保留 `E-1099` 兜底。

### Task B4（P1）：测试补齐

**Files:**
- Create/Modify: `backend/tests/test_inspiration_flow.py`
- Modify: `backend/tests/test_style_generation.py`
- Modify: `backend/tests/test_generation_worker.py`

**Step 1:** 首包欢迎语测试。  
**Step 2:** 混合输入测试（文本+图片+视频）。  
**Step 3:** 非视觉模型图片输入返回 `E-1010` 测试。  
**Step 4:** 单选点击直发推进测试。  
**Step 5:** 全量 `pytest -q` 全绿。

### Task B5（P2）：生成链路模式质量

**Files:**
- Modify: `backend/app/workers/generation_worker.py`

**Step 1:** 按 `content_mode` 分支提示词与文案模板。  
**Step 2:** `scenic` 不再走食材导向模板。  
**Step 3:** `food_scenic` 混合语义均衡输出。  
**Step 4:** 补回归测试。  
**Step 5:** 再次 `pytest -q`。

## 5. 前端任务清单（Owner: Frontend）

### Task F1（P1）：中间面板替换

**Files:**
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/components/InspirationPanel.tsx`
- Remove/Deprecate: `frontend/src/components/AssetInputPanel.tsx`, `frontend/src/components/StyleChatPanel.tsx`（按项目策略）

**Step 1:** 中间区替换为单面板“灵感对话”。  
**Step 2:** UI 结构对齐 Gemini Web 风格（大输入区 + 附件带 + 消息流）。  
**Step 3:** 支持图片/视频选择与文本输入同轮发送。  
**Step 4:** 展示附件缩略与标签。  
**Step 5:** 加入欢迎语渲染。

### Task F2（P1）：请求层与类型

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/store.ts`

**Step 1:** 新增 `getInspirationConversation`、`postInspirationMessage`。  
**Step 2:** 补全 `InspirationConversationResponse`、`InspirationMessage`、`InspirationDraft` 类型。  
**Step 3:** `multipart/form-data` 发送 `text + selected_items + images + videos`。  
**Step 4:** 错误码 `E-1010` 显示中文提示。  
**Step 5:** 移除旧 `styles/chat` 主路径依赖。

### Task F3（P1）：交互闭环与防重入

**Files:**
- Modify: `frontend/src/components/InspirationPanel.tsx`

**Step 1:** `options.max == 1` 点击即发。  
**Step 2:** `options.max > 1` 多选后确认提交。  
**Step 3:** `isLoading` 期间禁用输入、选项、发送按钮。  
**Step 4:** 修复点击输入框导致弹窗关闭/回主界面问题（事件冒泡治理）。  
**Step 5:** `npm run lint && npm run build` 全绿。

### Task F4（P2）：体验增强

**Files:**
- Modify: `frontend/src/components/InspirationPanel.tsx`
- Modify: `frontend/src/styles/*`（按项目现状）

**Step 1:** fallback 气泡标签可视化。  
**Step 2:** 附件删除、重传、进度提示。  
**Step 3:** 草案锁定后按钮语义与状态优化。  
**Step 4:** 生成前校验草案锁定态。  
**Step 5:** 回归验证。

## 6. 联调与验收

1. `GET /openapi.json` 显示新增 inspirations 路径与 schema。  
2. 前端端口 `7778`、后端端口 `8887` 固定联调。  
3. 关键验收：
   - 欢迎语存在。
   - 混合输入可用。
   - 非视觉模型图片输入返回 `E-1010`。
   - 单选点击即推进。
   - `pytest -q` 全绿。
   - `npm run lint && npm run build` 全绿。

## 7. 风险与规避

1. 风险：接口字段漂移导致并行阻塞。  
   规避：严格以 `docs/OPENAPI.JSON` 冻结字段，变更先改文档。  
2. 风险：图片能力识别不一致。  
   规避：统一依赖 `/models.capabilities` 中 `vision` 标识。  
3. 风险：旧组件残留逻辑造成重复请求。  
   规避：旧入口下线或明确仅兼容，主入口唯一化。

## 8. 可转发提示词

### 8.1 发给后端

你是 Savory Canvas 后端负责人，请严格按 `docs/plans/2026-02-23-inspiration-dialog-integration-plan.md` 执行 P1 后再执行 P2。  
硬性要求：
1. 只以 `docs/OPENAPI.JSON` 为契约，不得私扩字段。  
2. 先完成 inspirations 两接口：`GET /api/v1/inspirations/{session_id}`、`POST /api/v1/inspirations/messages`。  
3. 图片输入时必须校验 `text_model` 的 `vision` 能力，不满足返回 `400 + E-1010`。  
4. 保持错误体结构统一，`E-1099` 仅兜底未知异常。  
5. 全量回归 `pytest -q` 必须通过。  
交付格式：
1. 变更文件清单。  
2. 按任务编号（B1-B5）逐条说明。  
3. `pytest -q` 输出摘要。  
4. 契约变更说明（字段级）。

### 8.2 发给前端

你是 Savory Canvas 前端负责人，请严格按 `docs/plans/2026-02-23-inspiration-dialog-integration-plan.md` 执行 P1 后再执行 P2。  
硬性要求：
1. 中间区改为单一 `InspirationPanel`，替换旧素材区和风格对话区。  
2. 支持文本+图片+视频同轮混合发送到 `POST /api/v1/inspirations/messages`。  
3. 修复“单选点击无反应”：`options.max == 1` 必须点击即发。  
4. `isLoading` 时禁用所有输入与选项，避免并发重复请求。  
5. 收到 `E-1010` 必须展示中文提示“当前模型不支持图片解析，请切换为视觉模型后重试”。  
6. `npm run lint && npm run build` 必须通过。  
交付格式：
1. 变更文件清单。  
2. 按任务编号（F1-F4）逐条说明。  
3. lint/build 输出摘要。  
4. 与 `docs/OPENAPI.JSON` 对齐说明。
