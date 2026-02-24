# Savory Canvas 后端开发说明（BACKEND）

- 版本：1.2.2
- 日期：2026-02-23
- 适用对象：后端开发工程师
- 契约基准：`docs/OPENAPI.JSON`

## 1. 目标

后端负责提供稳定的异步任务型 API，覆盖会话、灵感对话、素材、转写、风格模板、生成、导出、模型配置全链路能力。前端仅通过 HTTP API 交互，不直接访问数据库。代码写在 `backend` 目录。单函数不超过 200 行，单文件不超过 1000 行，模块边界清晰。

## 2. 技术边界与约束

- 运行栈：Python + FastAPI + SQLite。
- 打包形态：本地 exe 单体应用中的服务层。
- 异步策略：长耗时流程统一异步任务，不允许同步阻塞返回完整结果。
- 模型路由固定两类：
  - `image_model`（必须具备 `image_generation` 能力）
  - `text_model`（必须具备 `text_generation` 能力）
- 当灵感对话包含图片输入时，`text_model` 还必须具备 `vision` 能力。
- `text_model` 全局唯一，不支持任务级覆盖。
- `api_protocol` 枚举仅允许：
  - `responses`
  - `chat_completions`

## 3. 核心领域模型

### 3.1 Session

- 字段：`id`、`title`、`content_mode`、`created_at`、`updated_at`

### 3.2 Asset

- 字段：`id`、`session_id`、`asset_type`、`content`、`file_path`、`status`、`created_at`
- `asset_type`：`food_name`、`scenic_name`、`text`、`image`、`video`、`transcript`

### 3.3 InspirationState

- 字段：`session_id`、`stage`、`draft_payload`、`draft_style_id`、`locked`、`updated_at`
- `stage`：`style_collecting`、`prompt_revision`、`asset_confirming`、`locked`

### 3.4 InspirationMessage

- 字段：`id`、`session_id`、`role`、`content`、`options`、`fallback_used`、`attachments`、`asset_candidates`、`style_context`、`created_at`

### 3.5 StyleProfile

- 字段：`id`、`session_id`、`name`、`style_payload`、`sample_image_preview_url`、`is_builtin`、`created_at`、`updated_at`
- `style_payload` 固定字段：`painting_style`、`color_mood`、`prompt_example`、`style_prompt`、`sample_image_asset_id`、`extra_keywords`

### 3.6 GenerationJob

- 字段：`id`、`session_id`、`style_profile_id`、`image_count`、`status`、`progress_percent`、`current_stage`、`stage_message`、`error_code`、`error_message`、`created_at`、`updated_at`

### 3.7 ExportTask

- 字段：`id`、`session_id`、`job_id`、`export_format`、`status`、`file_path`、`error_code`、`error_message`、`created_at`

### 3.8 ProviderConfig / ModelRoutingConfig

- 保持现有字段，新增能力约束校验（`image_generation` / `text_generation` / `vision`）。

## 4. 新增接口契约（必须实现）

### 4.1 `GET /api/v1/inspirations/{session_id}`

- 语义：获取会话灵感对话全量上下文；首次访问需自动初始化欢迎语。
- 响应：`InspirationConversationResponse`

### 4.2 `POST /api/v1/inspirations/messages`

- 语义：提交一轮灵感对话输入（支持文本+图片+视频混合）。
- `multipart/form-data` 字段：
  - `session_id`：string，必填
  - `text`：string，可选
  - `selected_items`：string[]，可选
  - `action`：string，可选，枚举 `continue` / `confirm_prompt` / `confirm_assets` / `revise_assets` / `use_style_profile` / `save_style` / `skip_save`
  - `image_usages`：string[]，可选，按顺序对应 `images`，枚举 `style_reference` / `content_asset`，未提供默认 `content_asset`
  - `images`：binary[]，可选
  - `videos`：binary[]，可选
- 响应：`InspirationConversationResponse`
- 规则：
  - 包含视频时自动触发转写并并入对话上下文。
  - 包含图片但 `text_model` 无 `vision` 能力时返回 `400 + E-1010`。
  - `attachments[].usage_type` 支持返回图片用途标签（`style_reference` / `content_asset`），用于后续生图参考图选择。
  - 可预期失败（结构非法、模型格式异常）允许 `fallback_used=true` 且返回 200。
  - `confirm_prompt` 后进入 `asset_confirming`，必须 `confirm_assets` 后才允许锁定。

### 4.3 兼容接口说明

- `POST /api/v1/styles/chat` 保留兼容，不再作为新 UI 主入口。
- 风格模板 CRUD 保持不变：`POST/GET/PATCH/DELETE /api/v1/styles...`
- 素材上传补充：`POST /api/v1/assets/image` 支持直接上传样例图并返回 `Asset(image)`。

### 4.4 `GET /api/v1/jobs/{job_id}/stages`

- 语义：查询任务阶段轨迹，返回每个阶段的状态与时间。
- 响应：`GenerationStageListResponse`

### 4.5 `GET /api/v1/jobs/{job_id}/asset-breakdown`

- 语义：查询任务资产拆解结果，返回来源素材与结构化提取信息。
- 响应：`JobAssetBreakdownResponse`
- 规则：
  - `source_assets` 为本次任务参与拆解的素材快照。
  - `extracted` 必含 `foods`、`scenes`、`keywords` 三组字段。

### 4.6 `GET /api/v1/jobs/{job_id}/results` 语义补充

- `images[].image_url` 必须是前端可直接访问的 `http/https` 地址。
- `images[].asset_refs` 必须返回提示词关联素材 ID 列表，不允许空数组占位。

## 5. 任务状态机（生成链路）

- `queued`、`running`、`partial_success`、`success`、`failed`、`canceled`
- 阶段顺序：`asset_extract` -> `asset_allocate` -> `prompt_generate` -> `image_generate` -> `copy_generate` -> `finalize`
- `image_generate` 阶段必须产出真实图片文件（如 `.png/.jpg`），禁止 `.txt` 占位。

## 6. 模块职责

### 6.1 API 层

- 输入校验、输出序列化、错误码映射。
- 不写业务算法。

### 6.2 Service 层

- 编排灵感对话状态推进、模型调用、降级、风格锁定。

### 6.3 Repository 层

- 负责 SQL 与数据映射。
- 对话状态与消息必须持久化，禁止仅内存态。

### 6.4 Worker 层

- 负责视频转写、生成任务、导出任务等长耗时流程。
- 支持重试与状态恢复。

## 7. 错误码规范

- `E-1001`：视频转写失败。
- `E-1002`：灵感对话模型返回结构异常（可预期，支持 fallback）。
- `E-1003`：资产不足。
- `E-1004`：生图部分失败。
- `E-1004` 同时用于可预期上游生图异常（HTTP 错误、网络异常、响应格式异常）。
- `E-1005`：导出失败。
- `E-1006`：模型配置缺失。
- `E-1010`：当前文字模型不支持图片解析。
- `E-1099`：未知内部错误（兜底）。

错误响应结构：

```json
{
  "code": "E-1006",
  "message": "请先完成模型设置",
  "details": {}
}
```

## 8. 与前端联调规则

- API 字段命名以 `docs/OPENAPI.JSON` 为唯一来源。
- 用户可见提示文案统一中文。
- 状态枚举禁止私扩，新增字段必须先更文档后落代码。
- 单选轮次必须做到“点即发”，后端需接受 `selected_items` 且允许 `text` 为空字符串。

## 9. 后端测试清单

- 会话 CRUD 测试。
- 灵感对话初始化欢迎语测试。
- 混合输入（文本+图片+视频）测试。
- 非视觉模型上传图片返回 `E-1010` 测试。
- 单选点击直发推进测试。
- 多选确认推进测试。
- 风格锁定与保存/跳过保存测试。
- `content_mode` 三模式生成链路测试。
- 全量回归：`pytest -q` 全绿。

## 10. 交付物清单

- 数据库迁移脚本（含灵感对话状态/消息表）。
- 新增灵感对话 API 与服务实现。
- 模型能力校验实现。
- 生成链路模式分支修复。
- 覆盖核心链路的自动化测试与联调记录。
