# 真 Agent 改造计划：从"路由器"到"创作主脑"

> 基线提交：9c437e3（main）  
> 目标：Agent 完全控制回复内容、选项、流程推进、生成触发和回滚  
> 设计原则：用户只需要表达想法，Agent 负责一切  
> 预计工作量：3-5 天

---

## 核心理念

**现状**：Agent 只决定"调哪个工具"，回复文案、选项按钮、阶段切换全部硬编码  
**目标**：Agent 决定一切——说什么、给什么选项、什么时候锁定、什么时候生成

```
现状流程：
  用户输入 → LLM选工具 → 工具执行 → 硬编码回复 → 硬编码按钮
                                      ↑ 浪费了 Agent 能力

目标流程：
  用户输入 → Agent 思考 → 可能调工具 → 可能再调工具 → Agent 组织回复 → Agent 决定选项
                                                      ↑ Agent 完全自主
```

---

## 架构变更总览

### 删除

| 删除项 | 原因 |
|---|---|
| `_build_turn_result` 中所有硬编码 reply/options | Agent 自己生成 |
| `InspirationDraft.stage` 的 Literal 枚举 | 不再有固定阶段 |
| 前端 `resolveActionValue()` 硬编码映射 | 选项由 Agent 动态生成 |
| 前端 `isBottomActionOption()` / `isSaveDecisionOption()` / `isAllocationOption()` 等 | 不再需要按文案匹配 |
| 后端 `action` 参数的 Literal 枚举限制 | Agent 理解自然语言 |
| 前端固定 4 步 stage 进度条 | Agent 用 dynamic_stage_label 替代 |

### 新增

| 新增项 | 说明 |
|---|---|
| Agent 二阶段执行：thinking + responding | 工具调用在 thinking 阶段完成，responding 阶段生成回复和选项 |
| Agent 返回结构化 options 带 `action_hint` | Agent 给每个选项附一个语义标签，前端原样回传 |
| `progress` 字段替代 `stage` | Agent 返回 0-100 进度 + 自然语言说明 |
| Agent 自动触发 `generate_images` | Agent 判断万事俱备时自动调用，不等用户确认 |
| Agent 回滚能力 | Agent 检测到用户改主意时主动清理 state 并回到合适位置 |
| system prompt 大幅增强 | 包含人格设定、决策框架、回复规范 |

---

## 详细任务

### Phase 1：后端 Agent 核心改造

#### P1-T1：Agent 二阶段执行模型

**核心改动：** 工具执行后不再直接构建结果，而是回到 Agent 让它组织回复。

**当前流程：**
```
agent_node(LLM决策) → tool_node(执行+硬编码结果) → agent_node(判断是否继续) → END
                                ↑ _build_turn_result 硬编码了 reply 和 options
```

**目标流程：**
```
agent_node(LLM决策) → tool_node(只执行，不生成回复) → agent_node(看到工具结果，决定继续还是回复)
                                                       ↓ 如果决定回复
                                                     LLM 生成 reply + options → END
```

**文件：`creative_agent.py`**

1. `_build_turn_result` 改名为 `_capture_tool_output`——只提取工具的结构化数据（asset_candidates、allocation_plan 等），不生成 reply 和 options

```python
def _capture_tool_output(self, tool_name: str, tool_result: Any, state: dict) -> dict:
    """只捕获工具的结构化输出，不生成用户回复。"""
    if tool_name == "suggest_painting_style":
        payload = tool_result if isinstance(tool_result, dict) else {}
        return {"style_payload": payload.get("style_payload"), ...}
    if tool_name == "extract_assets":
        return {"asset_candidates": tool_result if isinstance(tool_result, dict) else {}}
    if tool_name == "generate_style_prompt":
        payload = tool_result if isinstance(tool_result, dict) else {}
        return {"style_prompt": payload.get("style_prompt"), "image_count": payload.get("image_count")}
    if tool_name == "allocate_assets_to_images":
        return {"allocation_plan": tool_result if isinstance(tool_result, list) else []}
    if tool_name == "save_style":
        return {"style_saved": True, "style_name": (tool_result or {}).get("style_name")}
    if tool_name == "generate_images":
        return {"job_id": (tool_result or {}).get("job_id"), "job_status": "queued"}
    return {}
```

2. `_tool_node` 不再构建最终结果，只把工具输出合并到 request state

3. `_agent_node` 在第二次被调用时（已有工具结果），LLM 可以选择：
   - `respond_directly`：生成回复和选项返回给用户
   - `use_tool`：继续调用下一个工具（实现多工具串联）

4. Agent 的 `respond_directly` 返回结构：
```json
{
  "decision": "respond_directly",
  "reason": "已完成分图规划，向用户展示结果并提供选择",
  "result": {
    "reply": "我根据你提到的西安美食和城墙景点，把素材分配到了 3 张图里...",
    "options": [
      {"label": "很棒，就这样锁定吧", "action_hint": "confirm_and_lock"},
      {"label": "第二张图我想换个重点", "action_hint": "revise"},
      {"label": "能不能多加一张图？", "action_hint": "adjust_count"}
    ],
    "progress": 72,
    "progress_label": "分图预览"
  }
}
```

---

#### P1-T2：移除固定阶段，改为 Agent 进度

**文件：** `response.py`、`api.ts`、`inspiration_service.py`

1. `InspirationDraft.stage` 从 `Literal["style_collecting", ...]` 改为 `str`——Agent 返回任意阶段字符串
2. 新增 `InspirationDraft.progress: int | None`（0-100）和 `InspirationDraft.progress_label: str | None`
3. `_apply_agent_turn` 读取 Agent 返回的 `progress` 和 `progress_label` 写入 state
4. `_build_response` 把 progress 信息放到 draft 中

---

#### P1-T3：动态选项（options）改造

**目标：** Agent 返回的选项是结构化的 `{label, action_hint}`，前端原样渲染、原样回传。

**后端改动：**

1. `StyleOptionBlock` schema 改为：
```python
class AgentOption(BaseModel):
    label: str
    action_hint: str | None = None

class AgentOptionBlock(BaseModel):
    items: list[AgentOption]
```

2. `_apply_agent_turn` 把 Agent 返回的 `options` 原样存入消息
3. `_build_response` 中 `draft.options` 原样传到前端

**前端改动：**

1. 删除 `resolveActionValue()`——不再需要从中文文案推断 action
2. 删除 `isBottomActionOption()` / `isSaveDecisionOption()` / `isAllocationOption()`
3. 按钮点击时：发送 `action=option.action_hint`（如果有），同时 `selected_items=[option.label]`（保留自然语言）
4. 所有选项统一渲染在底部，不再区分 footer/allocation

**后端 `action` 参数改动：**

1. `action` 从 `Literal[...] | None` 改为 `str | None`——Agent 理解任意 action_hint
2. Agent 在 `_build_input_summary` 中看到 `action=confirm_and_lock`，自己判断该做什么

---

#### P1-T4：多工具串联

**文件：** `creative_agent.py`

**目标：** Agent 可以一轮连续调多个工具，用户只看到最终结果。

1. `_after_tool` 永远返回 `agent_node`（已经是这样，保持）
2. `_merge_result_into_request` 改为**累积合并**——多次工具的输出都保留在 state 中
3. `_agent_node` 在每次重入时看到完整的累积 state，决定是否继续
4. 只有当 Agent 选择 `respond_directly` 时才结束循环，此时 Agent 组织最终回复
5. `MAX_AGENT_TOOL_CALLS` 保持为 5，作为安全限制

**示例场景：**
```
用户: "我要做一个关于西安美食的 3 张图攻略"

Agent 第 1 轮: use_tool → suggest_painting_style
Agent 第 2 轮: 看到风格结果 → use_tool → extract_assets
Agent 第 3 轮: 看到资产结果 → use_tool → generate_style_prompt
Agent 第 4 轮: 看到提示词 → respond_directly:
  "我已经帮你完成了风格选择和素材整理！
   风格：手绘水彩插画 / 暖色调
   提取到的美食：羊肉泡馍、肉夹馍、凉皮
   提取到的景点：城墙、大雁塔、回民街
   
   下一步我可以帮你做分图安排，你觉得这个方向怎么样？"
  options: [
    {label: "方向很好，帮我分图", action_hint: "proceed_allocation"},
    {label: "我想换个绘画风格", action_hint: "change_style"},
    {label: "再加几个景点", action_hint: "add_content"}
  ]
```

---

#### P1-T5：自动触发生成

**文件：** `creative_agent.py`、system prompt

**目标：** Agent 判断"风格确认 + 提示词确认 + 分图确认"都完成后，自动调用 `generate_images`，不等用户确认。

1. System prompt 中加入判断标准：
   - `style_payload` 有效
   - `style_prompt` 非空
   - `allocation_plan` 非空且用户已确认
   - `draft_style_id` 存在
2. Agent 满足条件时主动调 `generate_images`，然后 `respond_directly`：
   "所有准备工作完成！我已经帮你启动了图片生成任务，你可以在右侧查看进度 🎨"

3. 前端收到 `job_id` 字段后自动更新右侧面板

---

#### P1-T6：Agent 回滚能力

**文件：** `creative_agent.py`、system prompt、`inspiration_service.py`

**目标：** 用户说"重新来"/"换个风格"/"刚才那个不对"时，Agent 自动回退。

1. 新增工具 `reset_progress`：
```python
@tool
def reset_progress(session_id: str, reset_to: str) -> dict[str, Any]:
    """重置创作进度到指定阶段，清除后续数据。"""
    # reset_to: "style" | "prompt" | "assets" | "allocation" | "all"
    return runtime.reset_progress(session_id=session_id, reset_to=reset_to)
```

2. `InspirationService.reset_progress` 根据 `reset_to` 清理对应 state 字段：
   - `"style"`: 清空 style_payload, style_prompt, allocation_plan, draft_style_id, locked=False
   - `"prompt"`: 清空 style_prompt, allocation_plan, locked=False
   - `"allocation"`: 清空 allocation_plan, locked=False
   - `"all"`: 回到初始 state

3. System prompt 引导 Agent 在检测到用户意图变化时使用此工具

---

### Phase 2：System Prompt 重写

#### P2-T1：Agent 人格和行为规范

**文件：** `backend/prompts/agent/creative_agent_system_prompt.txt`

完全重写 system prompt，包含：

1. **人格**：友好热情的创作助手，有温度、会鼓励、像一个懂设计的好朋友
2. **决策框架**：
   - 信息不足 → 友好提问引导
   - 信息足够 → 一口气串联多个工具，最后组织回复
   - 用户确认 → 推进到下一步
   - 用户犹豫 → 给建议和鼓励
   - 用户反悔 → 使用 reset_progress，不要有压力
3. **回复规范**：
   - 用自然、温暖的中文
   - 回复中要总结关键信息（不是机械地说"已完成"）
   - 适当使用 emoji
   - 给出 2-4 个选项，选项文案要具体（不是"继续"而是"帮我把泡馍放在第一张图"）
4. **options 规范**：
   - 每个 option 有 label（用户看到的中文）和 action_hint（语义标签）
   - label 要具体、有引导性
   - 提供至少一个"积极推进"和一个"修改调整"的选项
5. **进度规范**：
   - 返回 progress（0-100）和 progress_label
   - 0-20: 初始了解
   - 20-40: 风格确定
   - 40-60: 提示词生成
   - 60-80: 分图规划
   - 80-100: 生成与完成
6. **自动生成判断标准**：明确列出什么条件下自动触发 generate_images
7. **回滚引导**：什么语言模式被视为"用户想回退"

---

### Phase 3：前端适配

#### P3-T1：动态选项渲染

**文件：** `InspirationPanel.tsx`

1. 删除所有硬编码选项匹配函数（resolveActionValue、isBottomActionOption 等）
2. 选项从 `string[]` 改为 `{label, action_hint}[]` 渲染
3. 按钮点击：`handleSend(option.action_hint, [option.label])`
4. 所有选项统一在底部渲染，不再区分位置

#### P3-T2：动态进度条

**文件：** `InspirationPanel.tsx`

1. 删除固定 4 步进度条
2. 改为根据 `draft.progress`（0-100）渲染平滑进度条
3. 显示 `draft.progress_label` 作为当前阶段文案

#### P3-T3：自动刷新 job

**文件：** `InspirationPanel.tsx`、`store.ts`

1. 当 response 中包含 `job_id` 时（在 draft 或 agent meta 中），自动触发 `store.latestJob` 更新
2. 或者在 response.draft 中新增一个 `active_job_id`，前端检测到后自动开始 polling

#### P3-T4：移除 `action` Literal 限制

**文件：** `backend/app/api/v1/inspiration.py`、`frontend/src/api.ts`

1. 后端 `action: Literal[...] | None` 改为 `action: str | None`
2. 前端 TypeScript 类型同步更新

---

### Phase 4：测试

1. **Agent 自由回复测试**：验证 Agent 返回自然语言回复而非硬编码文案
2. **动态选项测试**：验证 Agent 返回 `{label, action_hint}` 格式的 options
3. **多工具串联测试**：验证 Agent 可以一轮调 2-3 个工具后 respond_directly
4. **自动生成触发测试**：验证 Agent 在条件满足时主动调 generate_images
5. **回滚测试**：验证 Agent 检测到"换个风格"时调用 reset_progress
6. **进度测试**：验证 progress 从 0 推进到 100
7. **前端渲染测试**：npm run build 通过

---

## 执行顺序

```
阶段 1（核心）: P1-T1 + P1-T2 + P1-T3 → 后端框架就绪
阶段 2（能力）: P1-T4 + P1-T5 + P1-T6 → Agent 能力补齐
阶段 3（灵魂）: P2-T1 → System Prompt（最关键的一步）
阶段 4（前端）: P3-T1 + P3-T2 + P3-T3 + P3-T4 → UI 适配
阶段 5（验证）: Phase 4 测试
```

---

## 风险和注意事项

| 风险 | 缓解 |
|---|---|
| Agent 回复质量不稳定（太长/太短/跑题） | System prompt 加入回复长度和内容规范 |
| Agent 选错工具或不调工具 | 工具描述要极其清晰，few-shot 示例 |
| 自动生成误触发（条件不充分就生成了） | 设置防御：draft_style_id + allocation_plan + locked 三重检查 |
| 多工具串联导致 token 爆炸 | MAX_AGENT_TOOL_CALLS=5 限制 + 输入精简 |
| LLM 返回的 options 格式不对 | 在 _parse_decision 中加入 options 格式校验和兜底 |
| 回滚太激进（用户随口一说就清空了） | system prompt 中强调：只有明确表达才回滚 |

---

## 给工程师的提示词

```
当前基线：9c437e3（main）

目标：把 Agent 从"路由器"升级为"创作主脑"——Agent 完全控制回复内容、
选项按钮、进度推进、自动触发生成、自动回滚。用户只需要表达想法，
Agent 负责一切。

核心改造（按顺序做）：

━━ Phase 1：后端 Agent 核心 ━━

P1-T1：Agent 二阶段执行
- _build_turn_result 改名 _capture_tool_output，只提取工具的结构化数据
  （asset_candidates、allocation_plan 等），不再生成 reply 和 options
- tool_node 执行后回到 agent_node，Agent 看到工具结果后自己决定：
  继续调工具 or respond_directly 并生成自然语言回复+动态选项
- Agent 的 respond_directly result 格式：
  {reply: "自然语言回复", options: [{label: "按钮文案", action_hint: "语义标签"}],
   progress: 72, progress_label: "分图预览"}

P1-T2：移除固定阶段
- InspirationDraft.stage 从 Literal 枚举改为 str（Agent 返回任意值）
- 新增 draft.progress（int 0-100）和 draft.progress_label（str）
- _apply_agent_turn 和 _build_response 同步适配

P1-T3：动态选项
- StyleOptionBlock 替换为 AgentOptionBlock：
  items: [{label: str, action_hint: str | None}]
- 后端 action 参数从 Literal 改为 str | None
- Agent 自由决定给几个选项、文案是什么

P1-T4：多工具串联
- _after_tool 保持回 agent_node（已有），_merge_result_into_request 改为累积合并
- 一轮可串联多工具，只有 respond_directly 时才返回用户
- MAX_AGENT_TOOL_CALLS=5 保持

P1-T5：自动触发生成
- Agent 判断 style 确认 + prompt 确认 + allocation 确认都完成后，
  自动调 generate_images，不等用户点按钮
- 前端收到 job_id 字段后自动更新右侧面板

P1-T6：Agent 回滚
- 新增 reset_progress 工具：
  reset_progress(session_id, reset_to="style"|"prompt"|"allocation"|"all")
- 清理对应 state 字段
- Agent 检测到"换个风格""重新来""那个不对"时主动调用

━━ Phase 2：System Prompt 完全重写 ━━

用具体的性格、决策框架、回复规范来重写 system prompt：
- 人格：友好热情的创作助手，像一个懂设计的好朋友，用温暖的中文，适当 emoji
- 回复规范：总结关键信息，不说"已完成"，而是描述具体做了什么
- options 规范：每个选项 {label, action_hint}，文案要具体有引导性，
  至少一个"推进"和一个"调整"选项
- progress 规范：0-20 初始了解，20-40 风格确定，40-60 提示词，60-80 分图，80-100 完成
- 自动生成标准：style + prompt + allocation 都确认后自动 generate_images
- 回滚引导：明确表达才回滚，随口一说不算

━━ Phase 3：前端适配 ━━

P3-T1：删除所有硬编码选项函数
- 删除 resolveActionValue、isBottomActionOption、isSaveDecisionOption、isAllocationOption
- 选项从 string[] 改为 {label, action_hint}[] 渲染
- 按钮点击：handleSend(option.action_hint, [option.label])

P3-T2：动态进度条
- 删除固定 4 步进度条
- 改为 draft.progress（0-100）平滑进度条 + draft.progress_label 文字

P3-T3：自动刷新 job
- response 包含 job_id 时自动触发 store.latestJob 更新

P3-T4：action 类型放宽
- 后端 Form: action: str | None
- 前端 TS 类型同步

━━ Phase 4：测试 ━━

- Agent 自由回复测试（不是硬编码文案）
- 动态选项格式测试（{label, action_hint}）
- 多工具串联测试（一轮 2-3 个工具）
- 自动生成触发测试
- reset_progress 回滚测试
- 进度 0→100 测试
- npm run build 通过
- pytest 全绿

完成后告诉我：
1. git diff --stat
2. 测试数
3. 改动要点

不要分批，一次性全部完成。做完后我来审核，
尤其会看 system prompt 的质量和 Agent 回复的自然度。
```

---

## 完成标准

- [ ] Agent 回复是自然语言，不是硬编码文案
- [ ] 底部按钮由 Agent 动态决定，前端无硬编码匹配
- [ ] 无固定阶段枚举，进度由 Agent 动态管理
- [ ] Agent 可以一轮串联 2-3 个工具
- [ ] Agent 满足条件时自动触发生成
- [ ] 用户说"换个风格"时 Agent 自动回退
- [ ] 前端进度条是 0-100 平滑动画
- [ ] pytest 全绿，npm run build 通过
- [ ] 端到端体验：用户从 0 开始到生成完成，全程只需要打字和点选项
