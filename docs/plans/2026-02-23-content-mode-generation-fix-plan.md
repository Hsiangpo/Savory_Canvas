# 内容模式生成链路修复计划（Round5）

## 1. 目标

- 修复“当前生成链路偏美食写死”的问题，确保三种会话模式都可用：
  - `food`（美食）
  - `scenic`（景点）
  - `food_scenic`（景点+美食）
- 保证前后端联调时，模式选择、素材输入、提示词构建、文案结构保持一致。

## 2. 已确认问题

1. 前端新建会话固定传 `content_mode='food'`，用户无法创建景点模式会话。
2. 生成 worker 未按 `content_mode` 分支，提示词仅做素材拼接。
3. 文案模板固定为“准备食材/烹饪步骤”，与 `scenic` 语义不匹配。
4. 现有素材类型缺少景点主实体类型，导致景点输入语义不清。

## 3. P1 修复范围（本轮必须完成）

### 3.1 契约变更（冻结）

1. `AssetTextCreateRequest.asset_type` 枚举新增 `scenic_name`。
2. `Asset.asset_type` 枚举新增 `scenic_name`。
3. 其余接口路径不变，错误结构不变。
4. 文档同步顺序固定：
   1) `docs/OPENAPI.JSON`
   2) `docs/BACKEND.MD`
   3) `docs/FRONTED.MD`

### 3.2 后端任务（Owner: Backend）

1. 扩展素材类型
   - 文件：`backend/app/schemas/request.py`、`backend/app/schemas/response.py`、`backend/app/services/asset_service.py`
   - 支持 `asset_type='scenic_name'` 入库。
2. 生成链路按会话模式分支
   - 文件：`backend/app/workers/generation_worker.py`
   - 在 `_run` 中读取 `session.content_mode`，传入提示词构建与文案构建逻辑。
   - `_build_prompts` 改为按模式选材：
     - `food`：优先 `food_name/text/transcript`
     - `scenic`：优先 `scenic_name/text/transcript`
     - `food_scenic`：融合 `food_name + scenic_name + text/transcript`
3. 文案模板模式化
   - `food`：保留“准备食材/烹饪步骤”。
   - `scenic`：改为景点导向（如“路线规划/拍摄建议”）。
   - `food_scenic`：输出混合结构（景点与美食各至少一段）。
4. 测试补齐
   - 新增/更新 `backend/tests`：
     - `food` 模式回归不变。
     - `scenic` 模式提示词不再依赖食材语义。
     - `food_scenic` 模式提示词含两类素材。
     - `scenic_name` 参数校验与入库通过。
5. 验收命令
   - `pytest -q` 必须全绿。

### 3.3 前端任务（Owner: Frontend）

1. 会话创建支持模式选择
   - 文件：`frontend/src/components/SessionPanel.tsx`
   - 新建会话弹窗新增 `content_mode` 下拉（中文标签）：
     - 仅美食（`food`）
     - 仅景点（`scenic`）
     - 景点+美食（`food_scenic`）
   - 创建请求必须传用户所选模式，不得写死 `food`。
2. 素材输入面板按模式展示
   - 文件：`frontend/src/components/AssetInputPanel.tsx`
   - `food`：显示“食品输入 + 文本 + 视频”。
   - `scenic`：显示“景点输入 + 文本 + 视频”。
   - `food_scenic`：显示“食品输入 + 景点输入 + 文本 + 视频”。
3. 新增景点输入请求
   - 调用 `POST /api/v1/assets/text` 时传 `asset_type='scenic_name'`。
4. 中文文案同步
   - 所有新增字段与提示保持中文，不引入英文面向用户文案。
5. 验收命令
   - `npm run lint && npm run build` 必须全绿。

## 4. P2 优化范围（P1 完成后执行）

1. 文本/转写中自动抽取景点与美食实体，减少手工输入负担。
2. 根据 `content_mode` 调整风格对话提示词倾向（景点构图、美食特写、混合叙事）。
3. 为 `food_scenic` 增加配比策略（例如 1:1 或按用户选择比例）。

## 5. 联调验收标准

1. 创建会话时可选 3 种模式，后端持久化正确。
2. `scenic` 模式可不填 `food_name` 也能正常生成。
3. `food_scenic` 模式生成结果中同时体现景点与美食语义。
4. `food` 模式行为与当前版本兼容，不产生回归。
5. 文档与运行接口一致，无“前端可选但后端不认”情况。

## 6. 执行顺序

1. 后端先完成契约与生成链路分支。
2. 前端并行完成会话模式与素材面板改造。
3. 双方以冻结契约联调，最后执行全量回归。
