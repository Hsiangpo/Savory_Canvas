# 右侧面板实时状态、图片可见性与文案质量修复计划（2026-02-24）

## 1. 问题与目标

### 1.1 当前问题

1. 任务进度区状态不够实时，用户无法清晰感知“当前执行到哪一步、何时切换到下一步”。
2. 结果区出现批量图片加载失败，用户只能看到报错占位。
3. 生成文案过于模板化，复杂度和可发布性不足。
4. 右侧“任务进度”区域占用过大，挤压“生成结果预览”可视空间。

### 1.2 本轮目标

1. 任务状态可实时、可追踪、可解释（进行中/成功/失败/时间点清晰）。
2. 修复图片加载链路，确保结果区优先展示真实可访问图片。
3. 文案改为大模型驱动生成，显著提升内容深度与结构完整性。
4. 优化右侧布局，兼顾进度可见性与结果预览效率。

## 2. 约束与冻结

1. 执行顺序：先 `P1` 再 `P2`。
2. `docs/OPENAPI.JSON` 作为唯一契约基准，不私扩字段。
3. 本轮优先在现有接口契约内修复行为与质量，不新增前端强依赖字段。
4. 用户可见文案统一中文。

## 3. 任务分配

## 3.1 后端任务（Owner: 后端）

### B1（P1）任务进度实时性增强

**目标：** 让前端在同一任务内持续看到状态推进，而不是长时间停在单一文案。

**修改文件：**
1. `backend/app/workers/generation_worker.py`
2. `backend/app/repositories/job_repo.py`

**改动要点：**
1. 在 `image_generate` 循环中按图片序号更新 `stage_message`（如“正在生成图片（2/4）”）并同步推进 `progress_percent`。
2. 在 `copy_generate` 阶段补充更细粒度状态文案（如“正在生成文案结构”“正在润色导语与结语”）。
3. 维持 `job_stage_log` 追加写入机制，但避免无意义重复消息（同阶段同文案短时间重复写入）。

**验收：**
1. `/api/v1/jobs/{job_id}` 在 running 期间 `stage_message` 可观察到递进变化。
2. `/api/v1/jobs/{job_id}/stages` 中阶段轨迹可读且时间序单调。

### B2（P1）图片加载失败链路修复

**目标：** `results.images[].image_url` 对前端 `<img>` 可直接访问。

**修改文件：**
1. `backend/app/services/generation_service.py`
2. `backend/app/infra/storage.py`
3. `backend/app/main.py`
4. `backend/tests/test_generation_flow.py`

**改动要点：**
1. 校验 `image_path -> image_url` 映射规则，确保统一落到 `/static/...` 可访问地址。
2. 在结果返回前增加文件存在性兜底校验：文件缺失时返回明确业务错误（不返回假 URL）。
3. 保持现有契约字段不变，仅修复 URL 可访问语义。

**验收：**
1. 新任务成功后，`image_url` 浏览器直接访问返回 200 且为图片内容。
2. 前端结果区不再批量触发 `onError`。

### B3（P1）文案生成质量升级（大模型驱动）

**目标：** 文案由模板改为模型生成，提升复杂度与可发布性。

**修改文件：**
1. `backend/app/workers/generation_worker.py`
2. `backend/app/services/model_service.py`
3. `backend/tests/test_generation_worker.py`

**改动要点：**
1. 将 `copy_generate` 从固定模板改为调用当前 `text_model` 生成结构化文案（title/intro/guide_sections/ending/full_text）。
2. 按 `content_mode`（food/scenic/food_scenic）注入不同写作目标、语气与信息密度要求。
3. 增加 JSON 结构校验与回退：模型不可用或结构非法时使用高质量降级模板，不影响任务成功语义。

**验收：**
1. 文案段落数量与信息密度明显高于当前模板。
2. `guide_sections` 非空，且每段有明确信息增量。

### B4（P2）后端观测与排障补强

**修改文件：**
1. `backend/app/workers/generation_worker.py`
2. `backend/tests/test_generation_flow.py`

**改动要点：**
1. 记录关键失败日志：`job_id/stage/provider_id/model_name/error_code`（不泄露密钥）。
2. 对“图片可访问但前端加载失败”的场景增加可复现测试（URL、MIME、文件存在性）。

### B5（P2）后端回归

**命令：**
1. `pytest -q`

**通过标准：**
1. 全绿通过。
2. 保留 warning 仅限上游依赖告警。

## 3.2 前端任务（Owner: 前端）

### F1（P1）进度区实时可视化优化

**修改文件：**
1. `frontend/src/store.ts`
2. `frontend/src/components/GeneratePanel.tsx`

**改动要点：**
1. running/queued 期间固定频率轮询 `job + stages + asset-breakdown`，直到终态。
2. 时间线显示当前阶段高亮、阶段状态图标、更新时间。
3. 处理“同阶段多条日志”的展示策略：保留最新一条为主，历史可折叠查看。

### F2（P1）结果图片加载失败修复

**修改文件：**
1. `frontend/src/components/ResultPanel.tsx`
2. `frontend/src/store.ts`

**改动要点：**
1. 图片请求增加一次重试（带 cache busting 参数），减少静态资源缓存导致的误失败。
2. 当单图失败时展示“重试加载”按钮，不影响其他图片渲染。
3. 保留中文错误提示，不展示技术噪音。

### F3（P1）右侧布局重排（给结果区留空间）

**修改文件：**
1. `frontend/src/components/GeneratePanel.tsx`
2. `frontend/src/components/ResultPanel.tsx`
3. `frontend/src/App.tsx`
4. `frontend/src/index.css`（或对应样式文件）

**改动要点：**
1. 将“任务进度”改为可折叠卡片，默认展开但设定最大高度与内部滚动。
2. 结果预览区最小高度提高，保证首屏可见至少 2 行图片格。
3. 移动端与窄屏下采用上下分区比例布局，避免进度区长期挤压结果区。

### F4（P2）文案展示层增强

**修改文件：**
1. `frontend/src/components/ResultPanel.tsx`

**改动要点：**
1. 文案区支持段落层级排版、标题与小节视觉区分。
2. 对长文案增加折叠/展开，避免右侧滚动体验恶化。

### F5（P2）前端回归

**命令：**
1. `npm run lint`
2. `npm run build`

## 4. 联调验收标准

1. 用户能看到任务从 `asset_extract` 到 `finalize` 的实时状态变化与时间轨迹。
2. 结果区图片可稳定加载，批量“图片加载失败”问题消失。
3. 文案复杂度显著提升，不再是两三句模板化输出。
4. 右侧布局中，结果区可见面积明显大于当前版本，进度区不再长期占满首屏。
5. 回归通过：
6. 后端：`pytest -q`
7. 前端：`npm run lint && npm run build`

## 5. 交付格式（前后端统一）

1. 变更文件清单（含路径）。
2. 按任务编号逐条说明（后端：B1-B5；前端：F1-F5）。
3. 命令输出摘要（pytest / lint / build）。
4. 契约变更说明（字段级；无则明确写“无”）。
