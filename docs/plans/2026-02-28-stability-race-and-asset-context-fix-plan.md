# 2026-02-28 稳定性修复计划（竞态与资产上下文）

## 背景

当前全量测试通过，但联调中仍出现以下高频问题：

1. 会话快速切换时，旧请求可能回写新会话状态，导致“处理中串会话”。
2. 资产提取在部分场景会被历史图片或历史文本污染。
3. 需求引导在“上一轮有图、这一轮纯文本”时可能丢失图片上下文。

本计划目标是在不改接口契约的前提下，补齐运行态一致性与上下文鲁棒性。

## 目标与范围

- 不修改 `docs/OPENAPI.JSON` 字段结构。
- 后端保持 LLM 主导决策，不引入规则化业务分支替代 LLM。
- 修复后满足：
  - 会话切换不串状态；
  - 资产提取优先使用最新有效上下文；
  - 需求引导可正确继承最近有效图片上下文；
  - 全量测试、前端构建、门禁通过。

## 任务拆分

### P1-1 前端会话切换竞态修复

- 文件：`frontend/src/store.ts`
- 改动：
  - `setActiveSessionId` 中对异步返回增加会话一致性校验（响应回写前二次确认当前 activeSessionId）。
  - `pollJobStatus` 增加 job/session 一致性校验，防止旧轮询结果覆盖当前会话。
- 验收：
  - 快速切换会话后，不再出现“新会话显示旧任务状态”。

### P1-2 图片上下文取样改为“最近优先”

- 文件：`backend/app/services/inspiration/flow_mixin.py`
- 改动：
  - `_collect_image_urls_from_assets` 调整为从最近上传图片中抽取最多 4 张（去重），不再固定取最早 4 张。
- 验收：
  - 新上传参考图优先进入 LLM 上下文。

### P2-1 需求引导继承历史图片上下文

- 文件：`backend/app/services/inspiration/requirement_mixin.py`
- 改动：
  - `_collect_latest_user_image_urls` 扫描策略改为“找到最近一条包含图片的用户消息再返回”，非图片消息不提前终止。
- 验收：
  - 用户先传图后纯文本补充时，模型仍能看到最近图片上下文。

### P2-2 文本资产上下文窗口化

- 文件：`backend/app/services/inspiration/flow_mixin.py`
- 改动：
  - `_build_asset_text_context` 对文本资产增加窗口限制（最近 N 条），降低历史需求污染。
- 验收：
  - 资产提取更聚焦当前轮需求，减少历史主题串入。

### P2-3 自动化回归测试补齐

- 文件：
  - `backend/tests/inspiration/test_inspiration_dialog_flow.py`
  - `backend/tests/inspiration/test_asset_extraction_priority.py`
- 覆盖点：
  - 最近图片上下文继承；
  - 最近图片优先采样进入分图规划；
  - 已有行为无回归。

## 验证清单

1. `pytest -q`
2. `npm run lint`
3. `npm run build`
4. `python scripts/check_limits.py`

## 风险与回滚

- 风险：上下文窗口过短导致信息不足。
- 缓解：窗口值采用保守上限（最近 8 条），并保留“用户本轮补充 + 近期用户上下文”。
- 回滚：若异常，可仅回退窗口化逻辑，不影响竞态与图片优先修复。

