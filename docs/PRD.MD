# Savory Canvas 产品需求文档（PRD）

- 版本：1.2.0
- 日期：2026-02-23
- 适用范围：个人本地使用软件（单机）
- 技术基线：FastAPI + React WebView + SQLite + Python 打包 exe

## 1. 产品目标与范围

### 1.1 产品目标

打造一款面向个人创作者的图文生成工具。用户可在“灵感对话”中输入文本、上传图片、上传视频（支持混合提交），系统自动完成视频转写、灵感收敛、风格草案确定、资产分配、图片生成、文案生成与导出，最终输出可直接发布的单篇图文帖。

### 1.2 成功标准（可验收）

- 任务创建成功率 >= 99%。
- 本地视频转写成功率 >= 95%。
- 多图任务完整返回率 >= 95%。
- 导出成功率（长图/PDF） >= 98%。
- 灵感对话首包（欢迎语）可用性 >= 99.9%。
- 单次任务平均可见首图等待时长 <= 45 秒（网络与模型服务正常时）。

## 2. 用户画像与核心场景

### 2.1 用户画像

- 用户类型：个人内容创作者。
- 主要目标：快速整理灵感并生成可发布图文，减少素材整理与文案撰写时间。
- 设备形态：Windows 本地桌面应用。

### 2.2 核心场景

- 场景 A：输入美食主题文本，生成多图与完整文案。
- 场景 B：输入景点主题文本，生成多图与完整文案。
- 场景 C：上传视频并自动转写后生成多图与完整文案。
- 场景 D：同一轮同时提交文本 + 图片 + 视频，完成灵感收敛并生成图文。

## 3. 关键业务定义

- 会话（Session）：一次完整创作上下文容器。
- 灵感对话（Inspiration Conversation）：素材输入与风格收敛的一体化对话流程。
- 素材（Asset）：输入或中间产物，包括食品名称、景点名称、文本、图片、视频、转写文本。
- 风格模板（StyleProfile）：用于约束画风、背景、氛围、张数等参数。
- 生成任务（GenerationJob）：异步执行链路实例。
- 结果（GenerationResult）：图片结果与结构化文案。
- 导出任务（ExportTask）：长图/PDF 的异步导出流程。

## 4. 端到端流程

### 4.1 主流程

1. 用户创建或进入会话。
2. 中间区展示“灵感对话”单一大面板（替代原素材区 + 风格配置双面板）。
3. 首次进入会话自动出现欢迎语：  
   “欢迎来到 Savory Canvas！说说你的想法吧，文字、图片、视频都可以，我会帮你整理成可生成的创作方案。”
4. 用户在同一输入区提交文本、图片、视频（支持混合）。
5. 若包含视频，系统先完成转写并将转写内容并入对话上下文。
6. 若包含图片且当前文字模型不具备视觉理解能力，系统返回明确提示并阻断该轮处理。
7. 智能体按阶段生成选项；用户可单选直发或多选后确认，先完成提示词确认，再完成资产确认，最后锁定草案。
8. 用户决定是否将草案保存为风格模板。
9. 系统提取资产（美食/景点条目），执行资产分配并创建生成任务。
10. 前端按任务进度展示，图片逐张回显。
11. 用户查看结果，执行一键复制文案或导出长图/PDF。

### 4.2 任务状态机

- `queued`：任务已创建，等待执行。
- `running`：任务执行中。
- `partial_success`：部分步骤或部分图片失败，但存在可用结果。
- `success`：全部步骤完成并可用。
- `failed`：任务失败，无可用结果。
- `canceled`：用户取消。

### 4.3 进度规范

- `progress_percent`：0-100。
- `current_stage`：`asset_extract`、`asset_allocate`、`prompt_generate`、`image_generate`、`copy_generate`、`finalize`。
- `stage_message`：前端可直接显示的中文进度文案。

## 5. 功能需求（FR）

### 5.1 会话与历史

- FR-001：支持创建会话与历史会话列表。
- FR-002：支持进入任意历史会话并恢复上下文。
- FR-003：会话内保存素材、灵感对话、任务、结果、导出记录。

### 5.2 灵感对话输入与转写

- FR-004：中间区统一为“灵感对话”面板，不再拆分素材区与风格对话区。
- FR-005：支持文本输入。
- FR-006：支持图片上传。
- FR-007：支持视频上传。
- FR-008：支持同一轮混合输入（文本 + 图片 + 视频任意组合）。
- FR-009：视频上传后自动触发本地转写，并把转写文本并入灵感对话上下文。
- FR-010：上传附件需在输入区上方展示（图片缩略、视频文件标签）。

### 5.3 灵感对话选项与风格收敛

- FR-011：后端按“阶段 + 会话上下文 + 本轮输入 + 已选项”实时生成选项，返回严格 JSON：
  - `reply`：对话文案。
  - `options.title`：选项标题。
  - `options.items`：可选项数组。
  - `options.max`：本轮可选上限。
- FR-011A：`options.items` 必须为动态生成结果，不允许长期返回固定模板集合；同一阶段应体现输入语义差异。
- FR-011B：`options.max == 1` 时，点击选项必须立即触发提交；`options.max > 1` 时必须“多选后确认提交”。
- FR-012：每轮模型回复都需可继续推进或确认；用户确认后进入草案锁定态。
- FR-013：锁定后不再允许继续修改草案参数，需显式提示“已锁定，可开始生成”。
- FR-013A：锁定前必须经过“提示词确认 + 资产确认”双确认。
- FR-014：支持将草案保存为风格模板，并支持风格模板重命名、删除。
- FR-015：当模型不可用、返回非预期结构或校验失败时，允许 fallback 并继续流程。
- FR-015A：fallback 场景必须返回 `fallback_used=true`，并在 `reply` 中给出明确中文降级提示。

### 5.4 模型能力约束

- FR-016：模型路由固定两类：
  - 生图模型（`image_model`，必须具备 `image_generation` 能力）
  - 文字模型（`text_model`，必须具备 `text_generation` 能力）
- FR-017：灵感对话处理图片时，`text_model` 必须具备视觉理解能力（`vision`）；否则返回明确中文错误提示。
- FR-018：支持查看当前提供商可用模型列表（`/models`），并包含能力字段供前端校验。

### 5.5 资产与分配

- FR-019：从多模态输入中提取美食与景点资产条目。
- FR-020：按图片张数分配资产，禁止同一资产在不同图片重复。
- FR-021：资产不足时，不强行凑满，前端提示“资产不足，建议减少图片张数”。

### 5.6 生成任务与结果

- FR-022：生成任务采用异步模式。
- FR-022A：支持取消运行中的生成任务。
- FR-023：多图任务需逐图回显。
- FR-024：文案输出固定结构：标题、导语、攻略分段、结尾。
- FR-025：支持一键复制文案。

### 5.7 导出

- FR-026：支持导出长图。
- FR-027：支持导出 PDF。
- FR-028：导出为异步任务，可查询进度与下载地址。

### 5.8 提供商与路由管理

- FR-029：支持模型提供商新增、编辑、删除、切换。
- FR-030：支持读取当前模型路由配置，用于前端回显。
- FR-031：文字模型为全局唯一，不支持任务级覆盖。

## 6. 非功能需求（NFR）

- NFR-001：所有前端面向用户字段必须为中文。
- NFR-002：前端必须防重复提交（按钮禁用 + 防抖）。
- NFR-003：长耗时接口必须异步化，禁止阻塞等待完整结果。
- NFR-004：后端错误码稳定、可映射中文错误提示。
- NFR-005：SQLite 读写操作需可重试，避免短暂锁冲突导致任务失败。
- NFR-006：任务执行过程需可恢复（程序重启后可继续查询历史状态）。
- NFR-007：灵感对话接口在模型服务正常时，95 分位首包响应时间 <= 3 秒。
- NFR-008：模型异常场景下灵感对话可用性 >= 99%（可预期异常支持降级）。

## 7. 数据模型定义

### 7.1 Session

- `id`（PK）
- `title`
- `content_mode`（`food` / `scenic` / `food_scenic`）
- `created_at`
- `updated_at`

### 7.2 Asset

- `id`（PK）
- `session_id`（FK）
- `asset_type`（`food_name` / `scenic_name` / `text` / `image` / `video` / `transcript`）
- `content`
- `file_path`
- `status`（`ready` / `processing` / `failed`）
- `created_at`

### 7.3 InspirationState

- `session_id`（PK, FK）
- `stage`（`style_collecting` / `prompt_revision` / `asset_confirming` / `locked`）
- `draft_style_id`（可空）
- `draft_payload`（JSON）
- `locked`（boolean）
- `updated_at`

### 7.4 InspirationMessage

- `id`（PK）
- `session_id`（FK）
- `role`（`assistant` / `user` / `system`）
- `content`
- `options`（JSON，可空）
- `fallback_used`（boolean）
- `attachments`（JSON）
- `created_at`

### 7.5 StyleProfile

- `id`（PK）
- `session_id`（可空，内置风格可无会话归属）
- `name`
- `style_payload`（固定结构 JSON：`painting_style`、`color_mood`、`prompt_example`、`style_prompt`、`sample_image_asset_id`、`extra_keywords`）
- `is_builtin`
- `created_at`
- `updated_at`

### 7.6 GenerationJob

- `id`（PK）
- `session_id`（FK）
- `style_profile_id`（FK）
- `image_count`
- `status`
- `progress_percent`
- `current_stage`
- `stage_message`
- `error_code`
- `error_message`
- `created_at`
- `updated_at`

### 7.7 ImageResult

- `id`（PK）
- `job_id`（FK）
- `image_index`
- `asset_refs`（JSON）
- `prompt_text`
- `image_path`
- `created_at`

### 7.8 CopyResult

- `id`（PK）
- `job_id`（FK）
- `title`
- `intro`
- `guide_sections`（JSON 数组）
- `ending`
- `full_text`
- `created_at`

### 7.9 ExportTask

- `id`（PK）
- `session_id`（FK）
- `job_id`（FK）
- `export_format`（`long_image` / `pdf`）
- `status`（`queued` / `running` / `success` / `failed`）
- `file_path`
- `error_code`
- `error_message`
- `created_at`

### 7.10 ProviderConfig

- `id`（PK）
- `name`
- `base_url`
- `api_key_masked`
- `api_protocol`（`responses` / `chat_completions`）
- `enabled`
- `created_at`
- `updated_at`

### 7.11 ModelRoutingConfig

- `id`（PK）
- `image_model_provider_id`
- `image_model_name`
- `text_model_provider_id`
- `text_model_name`
- `updated_at`

## 8. 接口能力矩阵（与 OpenAPI 对齐）

- 会话：`POST /api/v1/sessions`、`GET /api/v1/sessions`、`GET /api/v1/sessions/{session_id}`、`PATCH /api/v1/sessions/{session_id}`、`DELETE /api/v1/sessions/{session_id}`
- 灵感对话：`GET /api/v1/inspirations/{session_id}`、`POST /api/v1/inspirations/messages`
- 素材：`POST /api/v1/assets/text`、`POST /api/v1/assets/video`、`GET /api/v1/assets/{asset_id}/transcript`
- 风格模板：`POST /api/v1/styles`、`GET /api/v1/styles`、`PATCH /api/v1/styles/{style_id}`、`DELETE /api/v1/styles/{style_id}`
- 兼容接口（迁移期）：`POST /api/v1/styles/chat`（仅用于旧流程，后续下线）
- 生成任务：`POST /api/v1/jobs/generate`、`GET /api/v1/jobs/{job_id}`、`GET /api/v1/jobs/{job_id}/results`、`POST /api/v1/jobs/{job_id}/cancel`
- 导出：`POST /api/v1/exports`、`GET /api/v1/exports/{export_id}`
- 模型：`GET /api/v1/providers`、`POST /api/v1/providers`、`PATCH /api/v1/providers/{provider_id}`、`DELETE /api/v1/providers/{provider_id}`、`GET /api/v1/models`、`GET /api/v1/config/model-routing`、`POST /api/v1/config/model-routing`

## 9. 异常与降级策略

- E-1001：视频转写失败 -> 提示“视频转写失败，请更换视频或重试”。
- E-1002：灵感对话动态选项生成失败（模型异常/结构非法/校验失败） -> 使用 fallback 选项并提示“已启用默认候选方案”。
- E-1003：资产不足 -> 提示“当前可用资产不足，建议减少图片张数”。
- E-1004：生图部分失败 -> 任务置为 `partial_success`，返回已成功图片与文案。
- E-1005：导出失败 -> 保留在线浏览结果，提示“导出失败，请稍后重试”。
- E-1006：模型配置缺失 -> 阻止任务创建，提示“请先完成模型设置”。
- E-1010：图片理解能力缺失 -> 提示“当前模型不支持图片解析，请切换为视觉模型后重试”。

## 10. 验收标准

- AC-001：文本、图片、视频三类输入均可进入灵感对话流程，且支持混合提交。
- AC-002：首次进入会话可见欢迎语，且文案为中文。
- AC-003：视频上传后可完成转写并参与后续灵感收敛。
- AC-004：当文字模型不具备视觉能力时，上传图片返回 E-1010 且提示明确。
- AC-005：单选轮次点击选项后必须立即进入下一轮，不得出现“点击无反应”。
- AC-006：多选轮次可完成“选择 -> 确认 -> 下一轮”闭环。
- AC-007：灵感草案锁定后可直接开始生成，且可选择是否保存为风格模板。
- AC-008：`food`、`scenic`、`food_scenic` 三种会话模式均可产出语义匹配的图文。
- AC-009：多图任务可逐图回显，进度连续变化。
- AC-010：所有用户可见文案为中文。

## 11. 前后端并行开工边界

- 后端先交付：灵感对话接口、会话与素材持久化、模型能力校验、生成链路模式分支、错误码。
- 前端先交付：灵感对话统一面板、混合输入组件、附件显示、选项交互闭环、错误提示与状态管理。
- 双方联调基线：`docs/OPENAPI.JSON` 为唯一契约源。
