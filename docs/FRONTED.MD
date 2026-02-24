# Savory Canvas 前端开发说明（FRONTED）

- 版本：1.1.0
- 日期：2026-02-23
- 适用对象：前端开发工程师
- 契约基准：`docs/OPENAPI.JSON`

## 1. 目标

前端负责提供完整创作体验：会话切换、灵感对话、任务进度、结果展示、导出和模型设置。所有面向用户展示字段必须为中文，代码写在 `frontend` 目录。

## 2. 技术边界

- UI 宿主：React WebView（桌面应用内嵌）。
- 数据来源：仅通过后端 API 获取。
- 长任务策略：轮询任务状态，不阻塞 UI。

## 3. 页面信息架构

### 3.1 主布局

- 左侧：会话列表。
- 中间：灵感对话（替代旧素材区 + 风格配置区）。
- 右侧：任务进度与结果预览。

### 3.2 页面模块

- `SessionPanel`：会话创建、切换、重命名、删除与标题展示。
- `InspirationPanel`：文本输入、图片上传、视频上传、灵感对话选项、草案确认。
- `GeneratePanel`：创建生成任务、显示阶段进度。
- `ResultPanel`：多图展示、文案展示、一键复制。
- `ExportPanel`：长图/PDF 导出。
- `ModelPanel`：提供商与模型设置。

## 4. 状态管理模型

### 4.1 全局状态

- `activeSessionId`
- `sessionList`
- `providerList`
- `modelRouting`

### 4.2 会话状态

- `inspirationMessages[]`
- `inspirationDraft`
- `styleProfiles[]`
- `latestJob`
- `latestResult`

### 4.3 任务状态

- `jobStatus`：`queued`、`running`、`partial_success`、`success`、`failed`、`canceled`
- `progressPercent`
- `currentStage`
- `stageMessage`

## 5. 关键交互规范

### 5.1 灵感对话首包

- 首次进入会话调用 `GET /api/v1/inspirations/{session_id}`。
- 必须展示欢迎语：  
  “欢迎来到 Savory Canvas！说说你的想法吧，文字、图片、视频都可以，我会帮你整理成可生成的创作方案。”

### 5.2 混合输入

- 同一轮可同时发送文本 + 图片 + 视频。
- 上传附件在输入区上方展示为可视项（图片缩略、视频标签）。
- 视频上传后无需前端自行拼转写文本，后端会自动并入上下文。

### 5.3 选项交互（必须修复“点击无反应”）

- 严格渲染后端返回：
  - `reply`
  - `options.title`
  - `options.items`
  - `options.max`
- `options.max == 1`：点击即发送（立即请求，带 loading 锁）。
- `options.max > 1`：多选后点击“确认提交”再发送。
- 请求进行中必须禁用候选按钮与提交按钮，防止并发点击。
- 灵感阶段必须按顺序推进：`style_collecting -> prompt_revision -> asset_confirming -> locked`。
- `confirm_prompt` 后进入资产确认；仅 `confirm_assets` 才允许锁定并在右侧开始生成。

### 5.4 错误与降级展示

- 若 `fallback_used=true`，在气泡中展示“已启用默认候选方案”标签。
- 若后端返回 `E-1010`，展示“当前模型不支持图片解析，请切换为视觉模型后重试”。

### 5.5 任务进度与结果

- 轮询周期建议 1.5 秒。
- 按 `stage_message` 展示中文进度。
- 多图返回后逐图插入结果区，不等待全部完成。
- `images[].image_url` 语义固定为可直接访问的 `http/https` 图片地址。
- 可通过 `GET /api/v1/jobs/{job_id}/stages` 渲染阶段时间线。
- 可通过 `GET /api/v1/jobs/{job_id}/asset-breakdown` 展示素材拆解信息。

## 6. 中文文案规范（新增）

- `panel_inspiration_title`：灵感对话
- `btn_send`：发送
- `btn_confirm_selected`：确认提交
- `msg_fallback`：已启用默认候选方案
- `msg_vision_required`：当前模型不支持图片解析，请切换为视觉模型后重试
- `msg_transcript_failed`：视频转写失败，请更换视频或重试

## 7. 接口映射（前端调用点）

- 会话：
  - `POST /api/v1/sessions`
  - `GET /api/v1/sessions`
  - `GET /api/v1/sessions/{session_id}`
  - `PATCH /api/v1/sessions/{session_id}`
  - `DELETE /api/v1/sessions/{session_id}`
- 灵感对话：
  - `GET /api/v1/inspirations/{session_id}`
  - `POST /api/v1/inspirations/messages`
  - `action` 枚举：`continue` / `confirm_prompt` / `confirm_assets` / `revise_assets` / `use_style_profile` / `save_style` / `skip_save`
  - 图片用途标签：`image_usages[]`（与 `images[]` 一一对应，值：`style_reference` / `content_asset`）
  - 附件回显：`attachments[].usage_type`（图片附件可选返回）
- 素材兼容：
  - `POST /api/v1/assets/text`
  - `POST /api/v1/assets/video`
  - `GET /api/v1/assets/{asset_id}/transcript`
- 风格模板：
  - `POST /api/v1/styles`
  - `GET /api/v1/styles`
  - `PATCH /api/v1/styles/{style_id}`
  - `DELETE /api/v1/styles/{style_id}`
- 生成：
  - `POST /api/v1/jobs/generate`
  - `GET /api/v1/jobs/{job_id}`
  - `GET /api/v1/jobs/{job_id}/results`
  - `GET /api/v1/jobs/{job_id}/stages`
  - `GET /api/v1/jobs/{job_id}/asset-breakdown`
  - `POST /api/v1/jobs/{job_id}/cancel`
- 导出：
  - `POST /api/v1/exports`
  - `GET /api/v1/exports/{export_id}`
- 模型：
  - `GET /api/v1/providers`
  - `POST /api/v1/providers`
  - `PATCH /api/v1/providers/{provider_id}`
  - `DELETE /api/v1/providers/{provider_id}`
  - `GET /api/v1/models`
  - `GET /api/v1/config/model-routing`
  - `POST /api/v1/config/model-routing`

## 8. 模型设置页面规则

- 提供商管理区：新增、编辑、删除、启用切换。
- 前端提交 `api_protocol` 时仅允许以下枚举值：
  - `responses`
  - `chat_completions`
- 路由设置区仅允许两类模型：
  - 生图模型（`image_model`）
  - 文字模型（`text_model`）
- `GET /api/v1/models` 的 `capabilities` 需用于前端交互校验：
  - 有图片附件时，`text_model` 必须包含 `vision`。

## 9. 前端任务拆分

### 9.1 阶段 A：面板替换

- 将 `AssetInputPanel` + `StyleChatPanel` 合并为 `InspirationPanel`。
- 调整布局为单中间大面板。

### 9.2 阶段 B：混合输入与消息流

- 支持文本、图片、视频混合发送。
- 实现附件预览与发送队列。
- 打通 `GET/POST inspirations` 接口。

### 9.3 阶段 C：选项交互闭环

- 单选直发、复选确认、loading 锁。
- 修复“点击 1 张后无反应”。

### 9.4 阶段 D：生成与结果联动

- 读取灵感草案锁定态后再允许开始生成。
- 保持右侧任务与结果面板行为兼容。

## 10. 联调与验收清单

- 灵感对话首包欢迎语可见。
- 文本/图片/视频混合输入可走通。
- 非视觉模型上传图片可收到 `E-1010` 提示。
- 单选点击后立即推进，无“无反应”。
- 多选确认可推进且不重复请求。
- 所有用户可见文本均为中文。

## 11. 前端交付物

- `InspirationPanel` 组件实现。
- API 请求封装与类型定义。
- 状态管理改造。
- 联调记录与验收截图。
