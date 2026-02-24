# 2026-02-24 灵感对话二次重构计划（风格管理 + 资产双确认 + 一致性生图）

## 1. 目标

本轮把流程收敛为：`确定风格 -> 确定张数 -> 确定资产 -> 锁定生成`。
核心解决：
1. 增加风格管理入口（可选已有风格、可增删改查）。
2. 灵感对话必须经过“提示词确认 + 资产确认”双确认后才能锁定。
3. 图生图/文生图分流，提升多图风格一致性与鲁棒性。
4. 推文文案专业化，避免模板化输出。

## 2. 已确认决策

1. 风格库作用域：全局 + 会话混合。
2. 风格字段：固定契约（不再只用自由 JSON）。
3. 锁定闸门：必须双确认（提示词确认 + 资产确认）。
4. 参考图策略：模型自动判别 + 用户可覆盖。
5. 风格管理 UI：右侧抽屉。
6. 风格样例图来源：绑定已有 `Asset(image).asset_id`。
7. 文案目标风格：小红书攻略风。

## 3. 契约变更（先改 `docs/OPENAPI.JSON` 再开发）

1. 新增 `StylePayload` 固定字段：
   - `painting_style`
   - `color_mood`
   - `prompt_example`
   - `style_prompt`
   - `sample_image_asset_id`
   - `extra_keywords`
2. `StyleProfileCreateRequest` / `StyleProfileUpdateRequest` 支持固定字段更新。
3. `StyleProfile` 增加 `sample_image_preview_url`。
4. `InspirationDraft.stage` 扩展为：
   - `style_collecting`
   - `prompt_revision`
   - `asset_confirming`
   - `locked`
5. `POST /api/v1/inspirations/messages` 的 `action` 扩展：
   - `confirm_assets`
   - `revise_assets`
   - `use_style_profile`
6. `InspirationMessage` 增加结构化可选字段：
   - `asset_candidates`
   - `style_context`

## 4. 后端任务拆分

### P1（阻断）

- B1 文档先行：更新 `docs/OPENAPI.JSON`、`docs/PRD.MD`、`docs/BACKEND.MD`。
- B2 风格管理增强：`PATCH /styles/{style_id}` 支持更新固定风格字段；样例图 asset_id 做类型与归属校验。
- B3 灵感状态机重排：`style_collecting -> prompt_revision -> asset_confirming -> locked`；`confirm_prompt` 不得直接锁定。
- B4 资产确认闭环：从文本/图片/视频/转写提取资产候选，等待 `confirm_assets`。
- B5 图生图/文生图分流：
  - 文生图模型仅传 prompt。
  - 图生图模型传 prompt + 风格样例图 + 用户确认参考图。
  - 多图（>=2）从第 2 张开始附前一张结果图维持一致性。
- B6 提示词拆分：统一 `prompt_0`（风格母提示）+ `prompt_i`（每张资产填充），加入防拼贴约束。
- B7 失败续跑：单图失败自动补位重试，尽量补齐张数；失败保留 `partial_success` 可用结果。
- B8 错误与日志：可预期上游失败映射 `E-1004`，`E-1099` 仅兜底未知异常；日志记录 provider/model/endpoint/http_status/reason（不泄露密钥）。

### P2（质量）

- B9 content_mode 专业提示词模板库（food/scenic/food_scenic）。
- B10 风格一致性轻量评分与单次补救重绘。
- B11 推文文案结构升级（导语/路线/避坑/实用信息/结语）。

## 5. 前端任务拆分

### P1（阻断）

- F1 红框新增“风格管理”入口，右侧抽屉。
- F2 风格管理抽屉支持列表、新增、编辑、删除、应用。
- F3 灵感流程改为三步可见：风格确认、张数确认、资产确认。
- F4 资产候选编辑与确认按钮（确认资产/继续调整）。
- F5 上传图片标签化展示（风格参考图/内容素材图）并支持手动改判。
- F6 阶段完成态及时打勾，失败显式展示 `error_code/error_message`。
- F7 图片失败自动 + 手动重试，单图失败隔离。
- F8 文案区默认展开并结构化展示。

### P2（质量）

- F9 风格对齐可视化（风格摘要 + 参考图来源 + 资产分配摘要）。
- F10 阶段历史轨迹折叠查看与终态结论高亮。

## 6. 测试与验收

后端：
1. 新增/更新状态机与 action 测试（`confirm_prompt` 不得直接锁定，必须 `confirm_assets`）。
2. 新增图生图/文生图分流测试。
3. 新增多图前图参考传递测试。
4. 新增失败补位与 `partial_success` 语义测试。
5. `pytest -q` 全绿。

前端：
1. `npm run lint && npm run build` 全绿。
2. 联调验证：4 张任务不再拼贴成单图；失败态可见明确原因；阶段完成态及时勾选。

## 7. 约束

1. 只按 `docs/OPENAPI.JSON` 契约开发。
2. 字段变更先改文档再改代码。
3. 不私扩响应结构。
4. `E-1099` 仅未知异常兜底。

## 8. 剩余补齐项（2026-02-24 晚间复审）

### 8.1 P1（阻断）

- B12 参考图显式标注链路（后端）
  - 已在契约新增 `image_usages[]`（`style_reference` / `content_asset`）与 `attachments[].usage_type`。
  - 生成链路优先使用显式标注参考图，其次样例图，再次启发式兜底。
  - 验收：多图生成时参考图选择可追踪，日志可定位。

- F11 风格管理抽屉补齐搜索与样例图绑定（前端）
  - 增加风格列表搜索（名称/风格字段）。
  - 新增样例图选择器：从当前会话 image 资产中绑定 `sample_image_asset_id`。
  - 展示 `sample_image_preview_url` 预览。
  - 验收：可新增/编辑并保存样例图，应用后进入灵感流。

- F12 资产确认阶段可编辑（前端）
  - `asset_candidates` 需支持删除误提取项。
  - 提交“继续调整资产”时带回用户编辑后的结果（通过文本或结构化可序列化信息）。
  - 验收：用户可删改后再 `confirm_assets` 锁定。

### 8.2 P2（质量）

- B13 参考图判别策略增强（后端）
  - 当前启发式保留为最后兜底，主路径依赖显式标签 + 样例图。
  - 对不支持参考图参数的上游保持 prompt-only 自动回退。
  - 验收：失败不扩大到整任务，单图可补位重试。

- F13 前端稳定性与一致性修复（前端）
  - `ResultPanel` 禁止在渲染期调用 `setState`，改为 `useEffect`。
  - 移除 `draft?.image_count || 4` 等掩盖缺参的前端兜底。
  - 全量用户可见文案改为中文（去除英文混合标签）。
  - 验收：`npm run lint && npm run build` 全绿，交互无残留警告。

### 8.3 联调验收

1. 灵感对话上传图片时，前端可选择用途并透传 `image_usages[]`。
2. `GET /api/v1/inspirations/{session_id}` 中图片附件可回显 `usage_type`。
3. 生图时四张图提示词独立，非拼贴；失败态可继续补位到目标张数（或返回 `partial_success` 且可见原因）。
4. 任务轨迹中已完成阶段及时打勾，失败明确显示 `error_code + error_message`。

### 8.4 执行状态（2026-02-24 夜间）

1. 已完成（后端）：B12 参考图显式标注链路。  
   `image_usages[]` -> `attachments[].usage_type` -> 生成链路参考图选择已打通，且回归通过。
2. 已完成（前端）：F11 风格管理补齐。  
   已支持搜索、样例图绑定（`sample_image_asset_id`）与样例图预览（`sample_image_preview_url`）。
3. 已完成（前端）：F12 资产确认可编辑。  
   资产确认阶段支持删除误提取项，并以 `revise_assets` 回传修订结果。
4. 已完成（前端）：F13 稳定性与一致性。  
   移除了渲染期 `setState`，移除了 `image_count` 前端默认兜底值，交互文案统一中文。
