# 前端联调复核问题清单（2026-02-23 第二轮）

- 评审范围：`frontend/src` 与后端接口契约一致性、主流程可用性
- 复核结论：前端剩余 `1` 个 `P1` 问题；其余已提缺陷已修复

## 严重级别说明

- `P1`：影响核心流程，需本轮修复
- `P2`：质量改进项

## P1 问题

### FE-P1-005 提供商创建 `api_protocol` 枚举与后端契约不一致

- 证据：
  - `frontend/src/components/ModelPanel.tsx:23` 默认值为 `openai`
  - `frontend/src/components/ModelPanel.tsx:219` 选项值为 `openai`
  - `frontend/src/components/ModelPanel.tsx:220` 选项值为 `anthropic`
  - `frontend/src/api.ts:154` `addProvider` 入参把 `api_protocol` 定义为宽泛 `string`
  - `backend/app/schemas/request.py:59` 后端仅接受 `responses`/`chat_completions`
- 实测：
  - 发送 `api_protocol=openai` 到 `POST /api/v1/providers` 返回 `422`，错误码 `E-1099`，消息 `请求参数不合法`
- 影响：
  - 模型设置页“添加提供商”在默认路径下必定失败，阻塞后续模型路由配置与生成流程
- 修复建议：
  1. 将 UI 提交值改为后端枚举：`responses`、`chat_completions`
  2. UI 显示文案可保留中文（例如“Responses 协议”“Chat Completions 协议”），但 `value` 必须是后端枚举
  3. 收窄 TS 类型，避免再次出现契约漂移：
     - `api.ts` 中 `api_protocol` 改为联合类型 `\"responses\" | \"chat_completions\"`
     - `ModelPanel` 的 `newProvider` 状态类型同步收窄
- 验收标准：
  1. 在模型设置页使用默认选项可成功创建提供商（`201`）
  2. 创建后刷新列表可回显新提供商
  3. `npm run lint`、`npm run build` 继续通过

## 已验证通过项（供记录）

- `npm run lint`：通过
- `npm run build`：通过

