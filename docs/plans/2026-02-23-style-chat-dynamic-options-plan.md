# 风格对话动态选项改造计划（Round4）

## 1. 目标与范围

- 目标：将风格对话从“固定选项返回”升级为“智能体实时生成选项”，提升贴合度与可用性。
- 范围：仅改造风格对话链路（`POST /api/v1/styles/chat`）及其前端交互，不改变会话、任务、导出主流程。
- 联调基线：`docs/OPENAPI.JSON` 为唯一接口契约源，未更新契约前不得自行扩字段。

## 2. 现状与根因

- 当前后端在 `style_service` 内按阶段返回常量选项，无法根据用户输入动态变化。
- 当前前端仅支持“点一个就发送”，当 `options.max > 1` 时无法完成真实多选。
- 结果：用户感知为“选项写死”，对话智能性弱，且多选语义未闭环。

## 3. 参考实现思想（仅计划参考）

- 后端生成策略对齐 `E:\Develop\Masterpiece\Agent\Novel_Agent\backend\app\dreamweaver\service.py` 的核心思想：
  1. 结构化生成：按阶段输出 `reply + options(title/items/max)`。
  2. 严格校验：校验 JSON 结构、items 数量、max 合法性。
  3. 双层回退：模型输出异常时回退到默认选项，`fallback_used=true`，流程不中断。

## 4. 并行任务分配

| 优先级 | 负责人 | 任务 | 交付物 |
| --- | --- | --- | --- |
| P1 | 后端 | 风格对话动态生成引擎改造 | 可根据输入动态变化的 `StyleChatResponse` |
| P1 | 前端 | 多选交互闭环（支持 `max>1`） | 可选中多个选项后统一提交 |
| P1 | 前后端 | 契约与文档同步 | `docs/OPENAPI.JSON`、`docs/BACKEND.MD`、`docs/FRONTED.MD` |
| P2 | 后端 | 生成质量增强与上下文摘要优化 | 选项相关性与稳定性提升 |
| P2 | 前端 | fallback 可视化与交互细节优化 | 明确降级提示与更稳定的弹窗交互 |

## 5. 后端任务清单（Owner: Backend）

1. 改造 `backend/app/services/style_service.py`：
   - 替换固定常量直返逻辑，改为“阶段 + 会话上下文 + 用户输入”实时生成。
   - 保留现有响应字段：`reply/options/stage/next_stage/is_finished/fallback_used`。
2. 增加响应校验与回退：
   - 校验 `options.title/items/max` 必填；
   - 校验 `items` 非空、`max>=1` 且不超过 `items` 数量；
   - 校验失败或模型异常时走 fallback，`fallback_used=true`，`reply` 返回中文降级提示。
3. 增加阶段兼容：
   - 兼容 `init -> painting_style` 首包过渡，避免前端初始化阶段直接报错。
4. 增加测试（`backend/tests`）：
   - 动态生成成功用例（同阶段不同输入返回不同候选）。
   - 模型输出非法 JSON 时 fallback 用例。
   - 模型不可用时 fallback 用例。
   - `selected_items` + `max>1` 推进阶段用例。
5. 保持契约一致：
   - 若新增字段（如 `warning`）必须先更新 `docs/OPENAPI.JSON`，再落代码和前端类型。

## 6. 前端任务清单（Owner: Frontend）

1. 改造 `frontend/src/components/StyleChatPanel.tsx` 交互：
   - `options.max===1` 保持单击即提交；
   - `options.max>1` 启用复选 + “确认提交”按钮；
   - 超出上限时给出中文提示并阻止提交。
2. 请求与状态管理：
   - 提交体中的 `selected_items` 与后端语义一致；
   - 同步展示后端 `fallback_used` 结果提示，避免“看起来正常但已降级”。
3. 交互稳定性：
   - 修复输入框/弹窗点击冒泡导致误关闭问题；
   - 保存/提交按钮增加 loading 与防重复触发。
4. 前端回归：
   - `npm run lint`
   - `npm run build`

## 7. 契约与文档同步顺序（强约束）

1. 先更新 `docs/OPENAPI.JSON`（如有字段变化）。
2. 再更新 `docs/BACKEND.MD`（后端行为与错误语义）。
3. 再更新 `docs/FRONTED.MD`（前端提交规则与交互约束）。
4. 最后提交代码，避免前后端并行期间“口头契约”漂移。

## 8. 联调验收标准

1. 风格对话每轮返回选项可随用户输入变化，不再长期固定。
2. 当模型异常时，接口仍返回 200，且 `fallback_used=true`，前端可继续下一步。
3. `options.max>1` 时前端可完成多选提交，不会误触发单选直发。
4. 后端 `pytest -q` 全绿。
5. 前端 `npm run lint && npm run build` 全绿。

## 9. 执行顺序建议

1. 后端先完成动态生成 + fallback + 测试，提供稳定联调环境。
2. 前端并行完成多选交互与 fallback 提示展示。
3. 双方在冻结契约下联调，完成冒烟用例后合并。
