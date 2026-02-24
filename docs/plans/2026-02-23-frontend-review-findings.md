# 前端联调问题清单（2026-02-23）

- 评审范围：`frontend/src` 与后端实际接口行为对齐检查
- 结论：存在 3 个阻塞问题（P0），4 个重要问题（P1），1 个一般问题（P2）

## 严重级别说明

- `P0`：阻塞主流程，必须优先修复
- `P1`：影响核心体验或验收要求，需本轮修复
- `P2`：质量与可维护性问题，建议本轮一并修复

## P0 问题

### FE-P0-001 生成任务默认风格 ID 无效，导致任务创建直接失败

- 证据：`frontend/src/components/GeneratePanel.tsx:25`
- 当前行为：`startJob('default-style', 4)`
- 后端行为：风格不存在时返回 404（`未找到风格`）
- 影响：点击“开始生成”可稳定失败，主流程不可用
- 修复建议：
  1. 启动生成前要求用户选择真实 `style_profile_id`
  2. 若无风格则禁用按钮并展示中文提示
- 验收标准：
  1. 无风格时无法发起生成请求
  2. 选择风格后创建任务返回 202

### FE-P0-002 风格对话首包参数非法，接口直接 422

- 证据：`frontend/src/components/StyleChatPanel.tsx:47`
- 当前行为：首包 `stage='init'`
- 后端契约：仅允许 `painting_style/background_decor/color_mood/image_count`
- 影响：风格对话无法进入正常阶段流转
- 修复建议：
  1. 首包阶段改为 `painting_style`
  2. 或改成“先让用户发第一句再请求”
- 验收标准：
  1. 打开风格面板后首个请求返回 200
  2. 返回内容含 `reply/options/stage/next_stage`

### FE-P0-003 模型设置关键操作仍为 Mock，无法完成真实配置

- 证据：`frontend/src/components/ModelPanel.tsx:45`
- 证据：`frontend/src/components/ModelPanel.tsx:57`
- 当前行为：删除提供商/保存路由仅 alert，无真实 API 调用
- 影响：无法完成模型路由配置，后续生成流程可能因 `E-1006` 失败
- 修复建议：
  1. 补齐新增/删除/更新提供商 API 调用
  2. 补齐 `POST /config/model-routing` 调用
  3. 增加失败提示与表单校验
- 验收标准：
  1. 模型设置可真实持久化
  2. 重新打开面板可回显最新配置

## P1 问题

### FE-P1-001 导出面板未接入接口

- 证据：`frontend/src/components/ExportPanel.tsx:11`
- 证据：`frontend/src/components/ExportPanel.tsx:15`
- 当前行为：按钮仅展示，无请求
- 修复建议：接入 `POST /exports` + `GET /exports/{export_id}` 轮询，并支持下载
- 验收标准：长图/PDF 可触发、可查询、可下载

### FE-P1-002 视频上传后“成功提示”时机错误

- 证据：`frontend/src/components/AssetInputPanel.tsx:21`
- 证据：`frontend/src/components/AssetInputPanel.tsx:83`
- 当前行为：上传成功即提示“转写成功”
- 正确行为：上传成功后应轮询 transcript 状态，`ready` 才提示成功
- 验收标准：`processing/ready/failed` 三状态提示正确

### FE-P1-003 会话切换未恢复会话详情

- 证据：`frontend/src/store.ts:29`
- 当前行为：切换会话仅清空本地状态，不加载 `GET /sessions/{id}`
- 影响：历史素材、任务、导出记录无法恢复展示
- 修复建议：`setActiveSessionId` 后触发详情拉取并回填状态
- 验收标准：切换历史会话后，素材/任务/导出完整回显

### FE-P1-004 食品输入页仍是占位态

- 证据：`frontend/src/components/AssetInputPanel.tsx:98`
- 当前行为：“选择图片（建设中）”
- 修复建议：至少支持 `food_name` 文本提交（可先不做图片解析）
- 验收标准：食品输入可落库为 `food_name` 资产

## P2 问题

### FE-P2-001 质量门禁未过（Lint）

- 证据：`npm run lint` 报 6 errors + 2 warnings
- 典型位置：
  1. `frontend/src/components/AssetInputPanel.tsx:43`
  2. `frontend/src/components/ModelPanel.tsx:11`
  3. `frontend/src/components/ModelPanel.tsx:42`
  4. `frontend/src/components/ModelPanel.tsx:48`
  5. `frontend/src/components/ModelPanel.tsx:59`
  6. `frontend/src/components/StyleChatPanel.tsx:103`
- 修复建议：清理未使用变量、修复 hook 依赖警告
- 验收标准：`npm run lint` 0 error

## 建议修复顺序

1. FE-P0-001
2. FE-P0-002
3. FE-P0-003
4. FE-P1-001
5. FE-P1-002
6. FE-P1-003
7. FE-P1-004
8. FE-P2-001
