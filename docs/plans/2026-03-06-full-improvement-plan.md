# Savory Canvas 改进计划（Agent-First 版）

> 最后更新：2026-03-06 07:30  
> 审查范围：后端全量 Python 代码 + 前端全量 TypeScript/React 代码  
> 核心方针：**Agent 作为主脑，旧工作流仅保留为 fallback，不再往固定状态机里堆主逻辑**  
> 优先级：P0 = Agent 主脑化 | P1 = 剩余质量收尾 | P2 = 前端适配

---

## 📋 给工程师的提示词

> 以下内容可直接转发给你的工程师。

```
方向已确认：后续必须以 Agent 为主脑，旧流程只保留 fallback。
不要再继续写程序当头脑的代码。

核心原则：
1. 灵感对话的阶段推进必须由 Agent 决策主导，不是 InspirationService 的固定 if/else 分支
2. 旧的 _handle_collecting_stage / _handle_prompt_revision / _handle_asset_confirming 下沉为 fallback only
3. 前端从"固定四阶段硬编码"迁移到"Agent 返回的动态阶段 + trace + action schema"
4. 每个 Tool 的执行结果必须有真实的结果映射，不能走兜底
5. Agent System Prompt 要足够完备，能驱动完整的灵感创作流程
6. 保留 --legacy 启动参数，确保旧流程随时可回退

验证标准：
□ 不传 --legacy 时，Agent 模式可独立完成完整流程（风格对话 → 提示词确认 → 分图确认 → 锁定 → 生成）
□ 传 --legacy 时，旧流程仍然正常
□ pytest 全部通过
□ npm run build 无错误
□ 前端 Agent 阶段指示器和 trace 面板正常显示
```

---

## 已完成清单

以下任务已在之前的迭代中完成并通过审核：

### ✅ Agent 骨架（已完成）
- [x] LangGraph StateGraph + RunnableWithMessageHistory 基础骨架
- [x] `creative_agent.py`、`llm_provider.py`、`tools/creative_tools.py` 模块
- [x] 6 个 @tool 定义（suggest_painting_style, extract_assets, generate_style_prompt, allocate_assets_to_images, generate_images, generate_copy）
- [x] `--legacy` 启动参数切换
- [x] fallback 机制（Agent 异常时自动回退固定流程）
- [x] 前端 Agent 阶段指示器 + trace 可视化面板
- [x] InspirationAgentMeta / InspirationAgentTraceStep 响应模型
- [x] 未知工具 ValueError 保护 + 测试

### ✅ P1 功能缺陷修复（已完成）
- [x] SQLite WAL + busy_timeout + synchronous=NORMAL
- [x] long_image 真 PNG 导出（Pillow 拼接 + 文案叠加）
- [x] image_usages 生效（style_reference / content_asset）
- [x] 危险进程清理迁出到 start_dev.ps1
- [x] 新建会话时可选 content_mode
- [x] 导出面板增加长图入口

### ✅ P2 体验稳定性（已完成）
- [x] CORS 收口（去掉 allow_origin_regex）
- [x] SQLite 线程本地连接复用
- [x] 提示词误判修复（去掉宽泛 json 检测 + 正则多张）
- [x] 首次加载 / 创建会话走 setActiveSessionId 完整初始化
- [x] 复制失败 toast
- [x] Session/Model 弹窗 Escape 关闭

### ✅ P3 结构性重构（已完成部分）
- [x] 4 个巨型文件拆分（全部 < 800 行 + 行数守卫测试）
- [x] 统一 HTTP 客户端 `http_client.py`
- [x] Prompt 外部化（10 个模板 → `backend/prompts/`）
- [x] 请求链路追踪 `X-Request-Id`
- [x] conftest 硬编码改 pytest marker
- [x] Storage.save_* 统一返回相对路径
- [x] export_worker 字体跨平台 + 配置化
- [x] export_service._with_public_file_url 不再原地修改 dict
- [x] SessionUpdateRequest 支持 content_mode

---

## P0：Agent 主脑化（当前最高优先级）

> **核心目标：** 灵感对话的全流程由 Agent 自主决策驱动，InspirationService 的固定 if/else 分支降级为 fallback。

### P0-01 补齐 `_build_turn_result` 的工具结果映射
- **文件：** `backend/app/agent/creative_agent.py` 的 `_build_turn_result`
- **现状：** 只有 `suggest_painting_style` 有专属映射，其余 5 个工具走兜底（返回通用 reply）
- **要求：** 为每个工具实现真实的结果映射：
  - `extract_assets` → 返回 `asset_candidates` + 阶段推进到 `asset_confirming`
  - `generate_style_prompt` → 返回 `style_prompt` + `image_count` + 阶段推进到 `prompt_revision`
  - `allocate_assets_to_images` → 返回 `allocation_plan` + 提示用户确认
  - `generate_images` → 返回 `job_id` + `status` + 阶段推进到 `locked`
  - `generate_copy` → 返回 `job_id` + `status`
- **验证：** Agent 可以通过工具调用完成完整的阶段推进，不再依赖 InspirationService 的固定分支

### P0-02 增强 Agent System Prompt
- **文件：** `backend/app/agent/creative_agent.py` 的 `_build_system_prompt`
- **现状：** 只有一段简述（"二选一：respond_directly / use_tool"），缺乏对完整业务流程的指导
- **要求：** 把 System Prompt 改为完备的创作流程指引：
  - 明确定义 Agent 可使用的 6 个工具及其适用场景
  - 明确定义灵感对话的标准流程（风格收集 → 提示词生成 → 资产提取&分配 → 锁定生成）
  - 定义什么情况下应该 `respond_directly`（用户闲聊、询问进度时）
  - 定义什么情况下应该 `use_tool`（需要推进创作流程时）
  - 定义 `result` 对象的完整字段规范（reply, stage, locked, options, style_payload, asset_candidates...）
  - 把这个 System Prompt 外置到 `backend/prompts/agent/creative_agent_system_prompt.txt`
- **验证：** Agent 收到用户"我想做一篇西安美食攻略"时，能自主选择 `suggest_painting_style` 开始流程

### P0-03 Agent 驱动阶段推进（主路径切换）
- **文件：** `backend/app/services/inspiration_service.py` 的 `send_message` 方法
- **现状（约 129-165 行）：**
  ```python
  if self.agent_mode == "langgraph":
      try:
          agent_turn = self._run_agent_turn(...)
      except Exception:
          # fallback 到固定流程
      else:
          self._apply_agent_turn(...)
          return ...

  # 下面是固定流程（程序当头脑）
  stage = state.get("stage", "style_collecting")
  if stage == "prompt_revision": ...
  if stage == "asset_confirming": ...
  self._handle_collecting_stage(...)
  ```
- **目标：** Agent 模式下，固定分支**完全不执行**（除非 fallback）：
  - Agent 负责决定当前应该做什么（选工具还是直接回复）
  - Agent 的决策结果通过 `_apply_agent_turn` 写入 state
  - 只有 Agent 抛异常时才走固定分支
- **验证：** 在非 `--legacy` 模式下，`_handle_collecting_stage` / `_handle_prompt_revision` / `_handle_asset_confirming` 只在 fallback 时被执行

### P0-04 Agent 支持多轮工具调用
- **文件：** `backend/app/agent/creative_agent.py` 的 StateGraph
- **现状：** `tool_node` 之后直接 `END`，Agent 只能调用一次工具
- **要求：** 改为 `tool_node → agent_node`（循环），让 Agent 可以在一次请求中连续调用多个工具
  ```
  agent_node ──(use_tool)──→ tool_node ──→ agent_node ──(respond_directly)──→ END
  ```
- **安全措施：** 加最大循环次数限制（如 5 次），防止无限循环
- **验证：** Agent 可以在一次用户输入中先调 `extract_assets` 再调 `allocate_assets_to_images`

### P0-05 Agent 输入上下文丰富化
- **文件：** `backend/app/agent/creative_agent.py` 的 `_build_input_summary`
- **现状：** 只传了 session_id、stage、action、text、selected_items、attachments 的简单拼接
- **要求：** 补充关键上下文：
  - 当前 `style_payload` 概要（已选的绘画风格、色彩情绪等）
  - 当前 `asset_candidates` 概要（已确认的资产数量和类型）
  - 当前 `allocation_plan` 概要（已分配的图片数量）
  - 当前 `style_prompt`（已生成的母提示词）
  - 会话的 `content_mode`（food / scenic / food_scenic）
- **验证：** Agent 可以根据当前完整上下文做出合理的下一步决策

### P0-06 前端完全适配 Agent 动态阶段
- **文件：** `frontend/src/components/InspirationPanel.tsx`
- **现状：** Agent 模式下显示动态阶段标签，但仍有大量逻辑依赖固定四阶段（如锁定按钮的显隐逻辑）
- **要求：**
  - Agent 模式的选项按钮完全由 `agent.trace` 和 `draft.options` 动态驱动
  - 阶段指示器在 Agent 模式下只显示 Agent 返回的 `dynamic_stage_label`，不显示固定四步
  - "Agent 正在思考"的 loading 状态（当 API 请求中时显示 thinking indicator）
  - trace 面板默认展开（目前是 `<details>` 折叠的，改为默认展开或半展开）

---

## P1：剩余质量收尾

> 以下任务在 Agent 主脑化完成后再做，或者可以穿插进行。

### P1-01 领域 dataclass 替换
- 从 `Session` 和 `GenerationJob` 开始，在 `backend/app/domain/models.py` 定义 `@dataclass`
- Repository 层返回实例而非 dict
- 逐步替换 `dict[str, Any]`

### P1-02 Provider API Key 加密
- 使用 `Fernet` 对称加密存储
- mask 函数改为只保留尾 4 位

### P1-03 `request_id` 用 `contextvars` 关联到日志
- 让 `RequestIdFilter` 自动获取当前请求的 ID，而非始终输出 `-`

### P1-04 前端 InspirationPanel 拆分子组件
- `ChatAttachment.tsx`、`ChatMessage.tsx`、`CandidateEditor.tsx`、`ChatInput.tsx`

### P1-05 前端类型安全
- 封装 `getErrorMessage(err: unknown): string`
- `draft.style_payload` 类型改为 `api.StylePayload | null`

### P1-06 `pipeline_mixin.py` 决定去留
- 当前只剩 47 行，考虑合并到 `generation_worker.py` 或保留为薄包装

---

## 📊 任务统计

| 优先级 | 后端 | 前端 | 合计 |
| --- | --- | --- | --- |
| P0（Agent 主脑化） | 5 | 1 | 6 |
| P1（质量收尾） | 4 | 2 | 6 |
| **合计** | **9** | **3** | **12** |

---

## 📝 验收检查清单

### P0 完成后（Agent 主脑化）
- [ ] 不传 `--legacy` 时，灵感对话完整流程由 Agent 自主完成
- [ ] Agent 可根据上下文自主选择工具并推进阶段
- [ ] Agent 可在一次请求中连续调用多个工具
- [ ] 所有 6 个工具都有真实的结果映射
- [ ] System Prompt 外置到 `backend/prompts/agent/`
- [ ] 传 `--legacy` 时旧流程仍然完全正常
- [ ] 前端 Agent 模式下阶段指示器完全动态化
- [ ] pytest 全部通过
- [ ] npm run build 无错误

### P1 完成后
- [ ] 核心实体使用 dataclass
- [ ] API Key 加密存储
- [ ] 日志中 request_id 正确显示
- [ ] InspirationPanel 拆分为子组件
