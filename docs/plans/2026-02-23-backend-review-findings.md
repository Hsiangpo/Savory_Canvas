# 后端联调问题清单（2026-02-23）

- 评审范围：`backend/app` 与 `frontend/src` 联调契约稳定性
- 结论：无阻塞级故障（P0），存在 2 个重要问题（P1），2 个一般问题（P2）

## 严重级别说明

- `P0`：阻塞主流程
- `P1`：影响正确性或联调稳定性
- `P2`：健壮性/可维护性改进项

## P1 问题

### BE-P1-001 模型路由仅校验模型名称，不校验能力类型

- 证据：`backend/app/services/model_service.py:46`
- 当前行为：`image_model` 与 `text_model` 只检查 `model_name` 是否存在于 `DEFAULT_MODELS`
- 风险：可能把文本模型设置为生图模型，或反向设置，运行期才暴露问题
- 修复建议：
  1. 校验 `image_model.model_name` 必须包含 `image_generation`
  2. 校验 `text_model.model_name` 必须包含 `text_generation`
- 验收标准：
  1. 错配能力时返回 400 + 明确中文错误
  2. 新增对应单元测试

### BE-P1-002 风格对话阶段过于严格，缺少首包兼容策略

- 证据：`backend/app/schemas/request.py:21`
- 当前行为：`stage` 不接受 `init`
- 现状：前端曾使用 `init` 首包，导致直接 422
- 修复建议（二选一，推荐兼容）：
  1. 在后端将 `init` 映射为 `painting_style`
  2. 或维持严格校验，但补充明确错误提示 + 文档示例
- 验收标准：首包策略在 OpenAPI 与代码一致，联调不再出现阶段歧义

## P2 问题

### BE-P2-001 视频文件名拼接会重复扩展名

- 证据：`backend/app/api/v1/asset.py:31`
- 当前逻辑：`file_name = session_id + '_' + file.filename` 后又拼 `extension`
- 示例：`demo.mp4` 可能存为 `xxx_demo.mp4.mp4`
- 修复建议：
  1. 用 `Path(file.filename).stem` 生成主名
  2. 单次拼接扩展名
- 验收标准：上传 `abc.mp4` 最终文件名仅含一个 `.mp4`

### BE-P2-002 错误码粒度可进一步细化

- 证据：`backend/app/core/errors.py:22`
- 当前行为：多类 not found 统一为 `E-1099`
- 风险：前端难以做精细化提示与埋点
- 修复建议：
  1. 保留 `E-1099` 兜底
  2. 为常见资源缺失增加子错误码（如 `E-2001 会话不存在`）
- 验收标准：关键 404 场景返回可区分错误码

## 建议修复顺序

1. BE-P1-001
2. BE-P1-002
3. BE-P2-001
4. BE-P2-002
