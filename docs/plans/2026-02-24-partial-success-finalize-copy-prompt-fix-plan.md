# 2026-02-24 Partial Success / Finalize / 文案显示 / 提示词拆分修复计划

## 1. 背景与已确认现象

- 现场任务 `job_id=51f643ff-3ee1-4737-8de6-4ee3ad2e1ca8`：
  - `generation_job.status=partial_success`
  - `image_count=4`，`image_result` 实际仅 3 条
  - `copy_result` 实际存在 1 条（文案已生成）
  - `job_stage_log.finalize.status=partial_success`，前端当前按 `success/failed` 判图标，导致 `finalize` 看起来一直转圈。
- 当前每张图提示词包含“第 i/4 张”与样式字段，样式字段里混入了 `image_count` 等控制信息，容易诱导上游生成拼贴图（把多张需求合并成一张图）。

## 2. 目标

1. 上游偶发失败时尽量补齐目标张数（默认 4 张就尽量产出 4 张）。
2. 阶段轨迹语义清晰：完成态必须可见“已完成”而非持续转圈。
3. 部分成功场景下文案必须稳定可见。
4. 单图提示词必须是“单张目标”，避免四张需求被合并到一张图。

## 3. 约束

- 仅以 `docs/OPENAPI.JSON` 为契约基准，不新增字段。
- 错误语义保持：`E-1099` 仅兜底未知异常。
- 函数 <= 200 行，文件 <= 1000 行。

## 4. 前后端任务拆分

## 4.1 后端任务（P1）

### B1. 失败补位重试（生成张数补齐）
- 文件：`backend/app/workers/generation_worker.py`
- 改动：
  - 在 `_generate_images` 增加“按成功数补齐”策略：目标是成功数达到 `image_count`。
  - 为每个目标槽位增加重试上限（建议 `max_retry_per_slot=2`），总尝试次数增加硬上限（建议 `max_total_attempts=image_count * 3`），避免无限循环。
  - 每次重试写阶段日志：`正在生成图片（当前成功 x/目标 n，第 k 次尝试）`。
- 结果：
  - 上游短暂抖动不再直接导致 4 张请求只出 3 张。

### B2. 提示词拆分治理（防拼贴）
- 文件：`backend/app/workers/generation_worker.py`
- 改动：
  - `_build_prompt_specs` 去掉“第 i/n 张”表达，改成明确单图约束：
    - “只生成一张图片”
    - “禁止拼贴、九宫格、分镜、文字海报、拼版”
  - `_format_style_payload` 过滤控制字段：`image_count`、`style_prompt`、`force_partial_fail` 等非视觉风格字段不写入生图提示词。
  - 每张图增加轻微差异化子约束（构图角度/景别/镜头语言），避免重复图。
- 结果：
  - 四张图按四个独立提示词出图，不再把需求合并到单图拼贴。

### B3. finalize 阶段完成态语义
- 文件：`backend/app/workers/generation_worker.py`
- 改动：
  - `partial_success` 终态时，`finalize` 日志增加一次完成态日志（`log_status=success`），并在 `stage_message` 保留“任务完成，部分结果可用”。
- 结果：
  - 前端可稳定显示 finalize 已完成，同时保留“部分失败”语义。

### B4. 自动化测试补齐
- 文件：`backend/tests/test_generation_flow.py`
- 新增/更新：
  - 上游前几次失败后成功，最终成功张数可补齐到目标张数。
  - 提示词中不包含 `image_count` 文本、不包含“第 i/n 张”模板、不包含“拼贴”正向词。
  - partial_success 终态时 `finalize` 轨迹存在完成态日志。

## 4.2 前端任务（P1）

### F1. finalize 图标语义修正
- 文件：`frontend/src/components/GeneratePanel.tsx`
- 改动：
  - 阶段图标判定增加 `partial_success` 视为完成态（显示 ✓ 或“完成但有告警”图标）。
  - `finalize` 为最后阶段时，不能再显示长期 loading。

### F2. 部分成功结果可视化
- 文件：`frontend/src/components/ResultPanel.tsx`
- 改动：
  - 在结果区头部显示“已生成 3/4 张”这类可见状态。
  - 对缺失张数显示中文说明（上游失败导致），并保留已生成图片与文案展示。

### F3. 文案可见性增强
- 文件：`frontend/src/components/ResultPanel.tsx`
- 改动：
  - `partial_success` 时文案区默认展开一次，避免用户误判“没有推文文案”。
  - 文案区放到图片网格上方，或提供显著锚点跳转。

## 4.3 联调任务（P2）

### J1. 真机联调脚本
- 后端提供 2 个任务样本：
  - 样本 A：`status=success`，目标 4 张出满。
  - 样本 B：`status=partial_success`，明确展示“x/4 + 文案可见 + finalize 完成态”。
- 前端基于样本回归截图并对齐 UI 文案。

## 5. 验收标准

1. 后端 `pytest -q` 全绿。
2. 请求 4 张时，在上游可恢复场景下，最终成功张数应尽量补齐到 4。
3. `finalize` 不再出现“已结束但仍转圈”。
4. `partial_success` 下文案必须可见，且前端可读到“已生成 x/n”。
5. 四张图提示词为四个独立单图目标，不再出现“多图拼贴导向”。

## 6. 契约变更

- 本计划按“无接口字段新增”执行。
- 如实现过程中必须新增字段，先更新 `docs/OPENAPI.JSON` 再开发。
