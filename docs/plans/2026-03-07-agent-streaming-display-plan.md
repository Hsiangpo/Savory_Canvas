# Agent 实时思考流式展示改造计划

> 基线提交：当前 main HEAD  
> 目标：用户在对话框中实时看到 Agent 的每一步思考和工具调用过程  
> 预计工作量：后端 1 天，前端 1 天，联调 0.5 天

---

## 效果目标

```
用户发送："我想做一个关于西安美食的 3 张图攻略"

对话框实时显示：
┌─────────────────────────────────────────┐
│ 🧠 正在思考...                           │  ← 立刻出现
│                                         │
│ 🔧 正在分析风格方向                       │  ← 1-2 秒后
│ ✅ 风格分析完成                           │  ← 工具执行完
│                                         │
│ 🔧 正在提取美食和景点素材                  │  ← 接着出现
│ ✅ 已提取：羊肉泡馍、城墙、大雁塔          │  ← 工具执行完
│                                         │
│ 🧠 正在组织回复...                        │  ← 最后一轮思考
│                                         │
│ 我帮你整理了这些素材和风格方向...（最终回复）│  ← 最终消息替换上面
│ [按钮：方向很好，帮我分图] [换个风格]       │
└─────────────────────────────────────────┘
```

---

## 架构设计

### 技术选型：SSE（Server-Sent Events）

```
前端                              后端
  │                                │
  │  POST /messages/stream         │
  │  (FormData, 和原接口一样)       │
  │ ──────────────────────────────►│
  │                                │  creative_agent.respond_stream()
  │  event: thinking               │  ◄── agent_node 开始思考
  │ ◄──────────────────────────────│
  │                                │
  │  event: tool_start             │  ◄── 决定调工具
  │ ◄──────────────────────────────│
  │                                │  工具执行中...
  │  event: tool_done              │  ◄── 工具执行完
  │ ◄──────────────────────────────│
  │                                │
  │  event: thinking               │  ◄── 再次思考
  │ ◄──────────────────────────────│
  │                                │
  │  event: done                   │  ◄── 最终完整 response
  │ ◄──────────────────────────────│
  │                                │
```

### 为什么不改旧接口

- 旧 `POST /messages` 保持不变，测试和兼容不受影响
- 新增 `POST /messages/stream` 返回 SSE 流
- 前端切到新接口消费流式事件

---

## SSE 事件协议（前后端接口契约）

### 事件格式

每个事件遵循 SSE 标准格式：
```
event: <事件类型>
data: <JSON 字符串>

```
（注意：每个事件之间用一个空行分隔）

### 事件类型定义

#### 1. `thinking`
Agent 正在思考（LLM 调用开始）。

```
event: thinking
data: {"step": 1, "message": "正在分析你的需求..."}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| step | number | 当前步骤序号（从 1 开始） |
| message | string | 思考阶段的描述文案 |

#### 2. `tool_start`
Agent 决定调用工具，工具正在执行。

```
event: tool_start
data: {"step": 2, "tool_name": "extract_assets", "message": "正在提取美食和景点素材..."}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| step | number | 当前步骤序号 |
| tool_name | string | 工具名 |
| message | string | 用户友好的工具执行描述 |

#### 3. `tool_done`
工具执行完成。

```
event: tool_done
data: {"step": 2, "tool_name": "extract_assets", "message": "已提取素材：羊肉泡馍、城墙、大雁塔", "duration_ms": 2340}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| step | number | 当前步骤序号（和对应 tool_start 相同） |
| tool_name | string | 工具名 |
| message | string | 工具执行结果的用户友好摘要 |
| duration_ms | number | 工具执行耗时（毫秒） |

#### 4. `done`
Agent 完成所有处理，返回完整 response（和旧接口的 JSON response 结构完全一致）。

```
event: done
data: {"session_id": "...", "messages": [...], "draft": {...}, "agent": {...}}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| (整个 data) | InspirationConversationResponse | 和现有 POST /messages 返回的 JSON 结构完全一样 |

#### 5. `error`
执行过程中发生错误。

```
event: error
data: {"code": "E-1099", "message": "Agent 执行异常，请重试"}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| code | string | 错误代码 |
| message | string | 错误描述 |

### 完整事件序列示例

```
event: thinking
data: {"step":1,"message":"正在理解你的需求..."}

event: tool_start
data: {"step":2,"tool_name":"suggest_painting_style","message":"正在分析适合的绘画风格..."}

event: tool_done
data: {"step":2,"tool_name":"suggest_painting_style","message":"风格分析完成","duration_ms":1820}

event: thinking
data: {"step":3,"message":"正在决定下一步..."}

event: tool_start
data: {"step":4,"tool_name":"extract_assets","message":"正在提取美食和景点素材..."}

event: tool_done
data: {"step":4,"tool_name":"extract_assets","message":"已提取素材：羊肉泡馍、城墙、大雁塔","duration_ms":2150}

event: thinking
data: {"step":5,"message":"正在组织回复..."}

event: done
data: {"session_id":"abc","messages":[...],"draft":{...},"agent":{...}}

```

---

## 工具名到用户友好文案映射

后端生成 `message` 字段时，使用以下映射：

| tool_name | tool_start message | tool_done message 模板 |
|---|---|---|
| suggest_painting_style | 正在分析适合的绘画风格... | 风格分析完成 |
| extract_assets | 正在提取素材... | 已提取素材：{关键词前3个} |
| generate_style_prompt | 正在生成提示词... | 提示词已就绪 |
| allocate_assets_to_images | 正在规划分图方案... | 已生成 {N} 张图的分配方案 |
| save_style | 正在保存风格... | 风格「{name}」已保存 |
| generate_images | 正在创建生成任务... | 图片生成任务已启动 |
| reset_progress | 正在回退进度... | 已回退到{阶段}阶段 |
| generate_copy | 正在生成文案... | 文案生成任务已启动 |

---

## 后端任务

### BE-T1：creative_agent.py 新增 respond_stream 方法

**目标**：在 Agent 执行的每一步（agent_node 进入、tool_node 开始/结束）yield SSE 事件。

**改动：**

1. 新增 `respond_stream(request) -> Generator[dict, None, None]` 方法：

```python
def respond_stream(self, request: dict[str, Any]):
    """流式执行 Agent，每一步 yield 一个事件 dict。"""
    step = 0

    # 使用 LangGraph 的 stream() 替代 invoke()
    for graph_event in self._graph.stream(
        {
            "request": request,
            "input_messages": [HumanMessage(content=self._build_input_summary(request))],
        },
        config={"configurable": {"session_id": request["session_id"]}},
    ):
        # graph_event 格式: {node_name: state_update_dict}
        for node_name, state_update in graph_event.items():
            if node_name == "agent_node":
                step += 1
                decision = (state_update.get("decision") or {})
                if decision.get("decision") == "use_tool":
                    yield {
                        "event": "thinking",
                        "data": {"step": step, "message": decision.get("reason") or "正在决定下一步..."},
                    }
                else:
                    yield {
                        "event": "thinking",
                        "data": {"step": step, "message": "正在组织回复..."},
                    }
            elif node_name == "tool_node":
                # tool_start 在进入 tool_node 前发，tool_done 在 tool_node 完成后发
                # 由于 stream() 在 node 完成后才 yield，
                # 我们需要在 tool_node 内部用 callback 发 tool_start
                pass

    # 最终 result 从最后的 state 中获取
    final_result = ...
    yield {"event": "done", "data": final_result}
```

**注意**：LangGraph 的 `graph.stream()` 在每个 node 执行**完成后**才 yield。所以 `tool_start` 事件需要在 `_tool_node` 方法内部通过回调发出。

**推荐实现方式**：

```python
import queue
import threading

def respond_stream(self, request: dict[str, Any]):
    event_queue = queue.Queue()
    self._stream_event_queue = event_queue  # tool_node 内部可以访问
    step_counter = {"value": 0}
    self._stream_step_counter = step_counter

    def run_graph():
        try:
            result = self._graph.invoke(
                {
                    "request": request,
                    "input_messages": [HumanMessage(content=self._build_input_summary(request))],
                },
                config={"configurable": {"session_id": request["session_id"]}},
            )
            event_queue.put(("result", result))
        except Exception as e:
            event_queue.put(("error", e))
        finally:
            event_queue.put(("end", None))

    thread = threading.Thread(target=run_graph, daemon=True)
    thread.start()

    while True:
        event_type, payload = event_queue.get()
        if event_type == "end":
            break
        elif event_type == "error":
            yield {"event": "error", "data": {"code": "E-1099", "message": str(payload)}}
            break
        elif event_type == "sse":
            yield payload
        elif event_type == "result":
            # 处理最终结果
            ...
            yield {"event": "done", "data": final_response}
            break
    self._stream_event_queue = None
    self._stream_step_counter = None
```

在 `_agent_node` 和 `_tool_node` 内部：

```python
def _emit_stream_event(self, event: dict):
    q = getattr(self, "_stream_event_queue", None)
    if q:
        q.put(("sse", event))

def _next_step(self) -> int:
    counter = getattr(self, "_stream_step_counter", None)
    if counter:
        counter["value"] += 1
        return counter["value"]
    return 0

def _agent_node(self, state):
    step = self._next_step()
    self._emit_stream_event({"event": "thinking", "data": {"step": step, "message": "正在思考..."}})
    # ... 原有逻辑 ...

def _tool_node(self, state):
    tool_name = ...
    step = self._next_step()
    self._emit_stream_event({
        "event": "tool_start",
        "data": {"step": step, "tool_name": tool_name, "message": self._tool_start_message(tool_name)},
    })
    start_time = time.monotonic()
    tool_result = tool.invoke(tool_args)
    duration_ms = int((time.monotonic() - start_time) * 1000)
    self._emit_stream_event({
        "event": "tool_done",
        "data": {"step": step, "tool_name": tool_name, "message": self._tool_done_message(tool_name, tool_result), "duration_ms": duration_ms},
    })
    # ... 原有逻辑 ...
```

2. 新增私有辅助方法：
   - `_tool_start_message(tool_name: str) -> str`
   - `_tool_done_message(tool_name: str, tool_result: Any) -> str`
   - `_emit_stream_event(event: dict)`
   - `_next_step() -> int`

3. 非流式模式（原 `respond` 方法）**不变**，`_emit_stream_event` 在没有 queue 时静默跳过

---

### BE-T2：inspiration_service.py 新增 send_message_stream 方法

**目标**：包装 creative_agent.respond_stream()，在 done 事件时执行 _apply_agent_turn，返回完整 response。

```python
async def send_message_stream(
    self,
    *,
    session_id: str,
    text: str | None,
    selected_items: list[str],
    action: str | None,
    image_usages: list[str],
    images: list[UploadFile],
    videos: list[UploadFile],
) -> AsyncGenerator[str, None]:
    """SSE 流式版 send_message。yield 的每一行是 SSE 格式字符串。"""
    # 前置处理（和 send_message 完全一样）
    session = self.session_repo.get(session_id)
    if not session:
        raise not_found("会话", session_id)
    state = self._ensure_state(session_id)
    # ... 输入归一化、附件保存、用户消息追加 ...
    # （直接复用 send_message 的前半段逻辑，建议抽成 _prepare_message_context）

    request_payload = self._build_agent_request_payload(session, state, ...)

    # 流式执行 Agent
    final_result = None
    for event in self.creative_agent.respond_stream(request_payload):
        if event["event"] == "done":
            final_result = event["data"]
        else:
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"

    # done 事件：执行 _apply_agent_turn，构建完整 response
    if final_result:
        self._apply_agent_turn(session_id=session_id, state=state, turn=final_result)
        response = self._build_response(session_id, state)
        yield f"event: done\ndata: {json.dumps(response, ensure_ascii=False)}\n\n"
```

---

### BE-T3：新增 API 端点

**文件**：`backend/app/api/v1/inspiration.py`

```python
from fastapi.responses import StreamingResponse

@router.post("/inspirations/messages/stream")
async def post_inspiration_message_stream(
    session_id: str = Form(...),
    text: str | None = Form(default=None),
    selected_items: list[str] = Form(default=[]),
    action: str | None = Form(default=None),
    image_usages: list[Literal["style_reference", "content_asset"]] = Form(default=[]),
    images: list[UploadFile] = File(default=[]),
    videos: list[UploadFile] = File(default=[]),
    service: InspirationService = Depends(get_inspiration_service),
) -> StreamingResponse:
    async def event_generator():
        async for chunk in service.send_message_stream(
            session_id=session_id,
            text=text,
            selected_items=selected_items,
            action=action,
            image_usages=image_usages,
            images=images,
            videos=videos,
        ):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

**接口参数：和现有 POST /inspirations/messages 完全一致。**

**响应格式：text/event-stream（SSE）。**

---

### BE-T4：容错兜底

在 `respond_stream` 中加入容错：

```python
except Exception as e:
    yield {"event": "error", "data": {"code": "E-1099", "message": str(e)}}
```

在前端收到 `error` 事件时显示错误提示。

---

### BE-T5：测试

1. `test_stream_endpoint_returns_sse_events`：验证 /messages/stream 返回 text/event-stream
2. `test_stream_emits_thinking_before_tool`：验证先 yield thinking 再 yield tool_start
3. `test_stream_done_contains_full_response`：验证 done 事件包含完整 InspirationConversationResponse
4. `test_stream_error_on_agent_failure`：验证异常时返回 error 事件
5. `test_non_stream_endpoint_unchanged`：验证旧端点行为不变

---

## 前端任务

### FE-T1：api.ts 新增流式请求方法

```typescript
// SSE 事件类型定义
export interface StreamThinkingEvent {
  step: number;
  message: string;
}

export interface StreamToolStartEvent {
  step: number;
  tool_name: string;
  message: string;
}

export interface StreamToolDoneEvent {
  step: number;
  tool_name: string;
  message: string;
  duration_ms: number;
}

export interface StreamErrorEvent {
  code: string;
  message: string;
}

export type StreamEvent =
  | { type: 'thinking'; data: StreamThinkingEvent }
  | { type: 'tool_start'; data: StreamToolStartEvent }
  | { type: 'tool_done'; data: StreamToolDoneEvent }
  | { type: 'done'; data: InspirationConversationResponse }
  | { type: 'error'; data: StreamErrorEvent };

/**
 * 流式发送灵感消息，通过 callback 实时接收 Agent 思考过程。
 */
export async function postInspirationMessageStream(
  params: PostInspirationMessageParams,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const formData = new FormData();
  formData.append('session_id', params.session_id);
  if (params.text) formData.append('text', params.text);
  (params.selected_items || []).forEach((item) => formData.append('selected_items', item));
  if (params.action) formData.append('action', params.action);
  (params.images || []).forEach((img) => formData.append('images', img));
  (params.videos || []).forEach((vid) => formData.append('videos', vid));

  const response = await fetch('/api/v1/inspirations/messages/stream', {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({ message: '请求失败' }));
    throw new Error(errorBody.message || `HTTP ${response.status}`);
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    let currentEventType = '';
    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEventType = line.slice(7).trim();
      } else if (line.startsWith('data: ') && currentEventType) {
        const jsonStr = line.slice(6);
        try {
          const data = JSON.parse(jsonStr);
          onEvent({ type: currentEventType as StreamEvent['type'], data });
        } catch {
          // 忽略解析失败的行
        }
        currentEventType = '';
      }
    }
  }
}
```

---

### FE-T2：InspirationPanel.tsx 新增思考过程 UI 状态

1. 新增 state：

```typescript
interface ThinkingStep {
  id: number;           // step 序号
  type: 'thinking' | 'tool_start' | 'tool_done';
  message: string;
  toolName?: string;
  durationMs?: number;
  timestamp: number;    // 前端收到时间
}

const [thinkingSteps, setThinkingSteps] = useState<ThinkingStep[]>([]);
const [isStreaming, setIsStreaming] = useState(false);
```

2. 修改 `handleSend`：

```typescript
const handleSend = async (...) => {
  // ... 前置处理不变 ...
  setIsLoading(true);
  setIsStreaming(true);
  setThinkingSteps([]);

  try {
    await api.postInspirationMessageStream(
      { session_id: activeSessionId, text, selected_items: selection, action: actionStr, ... },
      (event) => {
        switch (event.type) {
          case 'thinking':
            setThinkingSteps((prev) => [...prev, {
              id: event.data.step,
              type: 'thinking',
              message: event.data.message,
              timestamp: Date.now(),
            }]);
            break;
          case 'tool_start':
            setThinkingSteps((prev) => [...prev, {
              id: event.data.step,
              type: 'tool_start',
              message: event.data.message,
              toolName: event.data.tool_name,
              timestamp: Date.now(),
            }]);
            break;
          case 'tool_done':
            setThinkingSteps((prev) => [...prev, {
              id: event.data.step,
              type: 'tool_done',
              message: event.data.message,
              toolName: event.data.tool_name,
              durationMs: event.data.duration_ms,
              timestamp: Date.now(),
            }]);
            break;
          case 'done':
            setMessages(event.data.messages || []);
            setDraft(event.data.draft || null);
            setAgentMeta(event.data.agent || null);
            if (event.data.draft?.active_job_id) {
              void syncLatestJob(event.data.draft.active_job_id);
            }
            break;
          case 'error':
            addToast(event.data.message, 'error');
            fetchConversation(activeSessionId);
            break;
        }
      },
    );
  } catch (error) {
    addToast(getErrorMessage(error), 'error');
    fetchConversation(activeSessionId);
  } finally {
    setIsStreaming(false);
    setIsLoading(false);
    setThinkingSteps([]);
  }
};
```

---

### FE-T3：思考过程气泡组件

新增组件 `ThinkingBubble.tsx`：

```tsx
interface ThinkingBubbleProps {
  steps: ThinkingStep[];
  isActive: boolean;  // 是否仍在进行中
}

export function ThinkingBubble({ steps, isActive }: ThinkingBubbleProps) {
  if (steps.length === 0 && !isActive) return null;

  return (
    <div className="thinking-bubble">
      {steps.map((step) => (
        <div key={`${step.id}-${step.type}`} className="thinking-step">
          <span className="thinking-icon">
            {step.type === 'thinking' && '🧠'}
            {step.type === 'tool_start' && '🔧'}
            {step.type === 'tool_done' && '✅'}
          </span>
          <span className="thinking-text">{step.message}</span>
          {step.durationMs && (
            <span className="thinking-duration">
              ({(step.durationMs / 1000).toFixed(1)}s)
            </span>
          )}
        </div>
      ))}
      {isActive && (
        <div className="thinking-step thinking-active">
          <span className="thinking-dots">
            <span>·</span><span>·</span><span>·</span>
          </span>
        </div>
      )}
    </div>
  );
}
```

**样式要求**：
- 气泡背景用 `rgba(255,255,255,0.04)` 半透明
- 每个步骤用 `fade-in` 动画（0.2s ease）
- `thinking-active` 的三个点有 CSS 跳动动画
- `tool_done` 对应步骤的 `tool_start` 行变灰（已完成态）
- 暗色主题下字体颜色用 `var(--text-secondary)`

---

### FE-T4：在对话流中渲染思考气泡

在 `InspirationPanel.tsx` 的消息列表底部、输入框上方：

```tsx
{/* 消息列表 */}
{messages.map((msg) => <ChatMessage ... />)}

{/* 实时思考过程（只在 streaming 时显示） */}
{isStreaming && (
  <ThinkingBubble steps={thinkingSteps} isActive={isStreaming} />
)}

{/* 底部操作区 */}
```

消息列表自动滚动到底部（已有逻辑，确认 ThinkingBubble 也触发滚动）。

---

### FE-T5：降级处理

如果流式请求失败（网络断开、浏览器不支持 ReadableStream 等），自动回退到旧的非流式接口：

```typescript
try {
  await api.postInspirationMessageStream(params, onEvent);
} catch (streamError) {
  // 降级到旧接口
  const response = await api.postInspirationMessage(params);
  setMessages(response.messages || []);
  setDraft(response.draft || null);
  setAgentMeta(response.agent || null);
}
```

---

## 完成标准

- [ ] Agent 每一步思考和工具调用都能在对话框中实时显示
- [ ] 每个步骤有进入/完成动画
- [ ] 工具执行完成后显示耗时
- [ ] 所有步骤完成后，思考气泡消失，最终消息正常显示
- [ ] 如果 Agent 连续调 3 个工具，用户能实时看到 3 次工具执行过程
- [ ] 流式失败时自动降级到旧接口
- [ ] 旧 POST /messages 接口行为完全不变
- [ ] pytest 全绿，npm run build 通过
