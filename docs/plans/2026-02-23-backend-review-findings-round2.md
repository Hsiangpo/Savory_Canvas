# 后端联调复核问题清单（2026-02-23 第二轮）

- 评审范围：`backend/app` 接口实现、错误处理与前端联调契约
- 复核结论：后端代码层未发现新增阻塞缺陷；存在 `1` 个 `P2` 契约可发现性问题

## 严重级别说明

- `P2`：非阻塞，但会增加联调返工概率，建议本轮修复

## P2 问题

### BE-P2-003 提供商协议枚举在开发文档中缺少显式说明，已引发联调偏差

- 证据：
  - `backend/app/schemas/request.py:59` 实际约束为 `\"responses\" | \"chat_completions\"`
  - `docs/BACKEND.MD:104` 仅列出字段名，未给出 `api_protocol` 可选值
  - `docs/FRONTED.MD:156` 描述了“提供商管理区”，但未声明 `api_protocol` 枚举
- 影响：
  - 前端按 `openai/anthropic` 实现后触发 `422`，导致“新增提供商”联调失败
- 修复建议：
  1. 在 `docs/BACKEND.MD` 增加 `api_protocol` 枚举说明与请求示例
  2. 在 `docs/FRONTED.MD` 的“模型设置页面规则”补充提交值约束（仅 `responses`/`chat_completions`）
  3. 补一条契约测试：校验非法 `api_protocol` 返回 `422 + E-1099`
- 验收标准：
  1. 文档与 OpenAPI、后端校验一致
  2. 前后端按文档实现后可一次联调通过

## 已验证通过项（供记录）

- `pytest -q`：`11 passed, 1 warning`
- 路由数与主流程接口数量未回退

