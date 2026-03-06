from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal, TypedDict

from langchain_community.chat_message_histories import SQLChatMessageHistory
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.history import RunnableWithMessageHistory
from langgraph.graph import END, START, StateGraph

from backend.app.core.prompt_loader import load_prompt
from backend.app.core.utils import new_id, now_iso

MAX_AGENT_TOOL_CALLS = 5


class CreativeAgentState(TypedDict, total=False):
    request: dict[str, Any]
    input_messages: list[BaseMessage]
    history_messages: list[BaseMessage]
    decision: dict[str, Any]
    result: dict[str, Any]
    agent_messages: list[BaseMessage]
    trace: list[dict[str, Any]]
    tool_call_count: int


@dataclass
class CreativeAgent:
    llm_provider: Any
    tools: dict[str, Any]
    db_path: Path

    def __post_init__(self) -> None:
        self._stream_local = threading.local()
        workflow = StateGraph(CreativeAgentState)
        workflow.add_node("agent_node", self._agent_node)
        workflow.add_node("tool_node", self._tool_node)
        workflow.add_edge(START, "agent_node")
        workflow.add_conditional_edges("agent_node", self._should_continue, ["tool_node", END])
        workflow.add_conditional_edges("tool_node", self._after_tool, ["agent_node", END])
        self._graph = workflow.compile()
        self._runnable = RunnableWithMessageHistory(
            RunnableLambda(self._invoke_graph),
            self._get_session_history,
            input_messages_key="input_messages",
            history_messages_key="history_messages",
            output_messages_key="agent_messages",
        )

    def respond(self, request: dict[str, Any]) -> dict[str, Any]:
        response = self._runnable.invoke(
            {
                "request": request,
                "input_messages": [HumanMessage(content=self._build_input_summary(request))],
            },
            config={"configurable": {"session_id": request["session_id"]}},
        )
        result = dict(response.get("result") or {})
        if "reply" in result and isinstance(result.get("reply"), str):
            result["reply"] = result["reply"].strip()
        decision = response.get("decision") or {}
        if "dynamic_stage" not in result and decision.get("dynamic_stage") is not None:
            result["dynamic_stage"] = decision.get("dynamic_stage")
        if "dynamic_stage_label" not in result and decision.get("dynamic_stage_label") is not None:
            result["dynamic_stage_label"] = decision.get("dynamic_stage_label")
        result["trace"] = response.get("trace") or []
        return result

    def respond_stream(self, request: dict[str, Any]) -> Iterator[dict[str, Any]]:
        event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        def run_graph() -> None:
            self._stream_local.event_queue = event_queue
            self._stream_local.step_counter = {"value": 0}
            try:
                response = self._runnable.invoke(
                    {
                        "request": request,
                        "input_messages": [HumanMessage(content=self._build_input_summary(request))],
                    },
                    config={"configurable": {"session_id": request["session_id"]}},
                )
                result = dict(response.get("result") or {})
                if "reply" in result and isinstance(result.get("reply"), str):
                    result["reply"] = result["reply"].strip()
                decision = response.get("decision") or {}
                if "dynamic_stage" not in result and decision.get("dynamic_stage") is not None:
                    result["dynamic_stage"] = decision.get("dynamic_stage")
                if "dynamic_stage_label" not in result and decision.get("dynamic_stage_label") is not None:
                    result["dynamic_stage_label"] = decision.get("dynamic_stage_label")
                result["trace"] = response.get("trace") or []
                event_queue.put(("result", result))
            except Exception as exc:
                event_queue.put(("error", exc))
            finally:
                if hasattr(self._stream_local, "event_queue"):
                    delattr(self._stream_local, "event_queue")
                if hasattr(self._stream_local, "step_counter"):
                    delattr(self._stream_local, "step_counter")
                event_queue.put(("end", None))

        worker = threading.Thread(target=run_graph, daemon=True)
        worker.start()

        while True:
            event_type, payload = event_queue.get()
            if event_type == "sse":
                yield payload
                continue
            if event_type == "result":
                yield {"event": "result", "data": payload}
                break
            if event_type == "error":
                message = str(payload) or "Agent 执行异常"
                yield {"event": "error", "data": {"code": "E-1099", "message": message}}
                break
            if event_type == "end":
                break

    def _invoke_graph(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._graph.invoke(payload)

    def _get_session_history(self, session_id: str) -> SQLChatMessageHistory:
        connection = f"sqlite:///{self.db_path.as_posix()}"
        return SQLChatMessageHistory(session_id=session_id, connection=connection)

    def _agent_node(self, state: CreativeAgentState) -> dict[str, Any]:
        model = self.llm_provider.build_chat_model()
        request = state.get("request") or {}
        tool_call_count = int(state.get("tool_call_count") or 0)
        step = self._next_step()
        self._emit_stream_event(
            {
                "event": "thinking",
                "data": {
                    "step": step,
                    "message": self._thinking_message(request),
                },
            }
        )
        messages = [
            SystemMessage(content=self._build_system_prompt()),
            *state.get("history_messages", []),
            HumanMessage(content=self._build_input_summary(request)),
        ]
        if tool_call_count >= MAX_AGENT_TOOL_CALLS:
            messages.insert(1, SystemMessage(content="你本轮已经达到最多 5 次工具调用限制，必须直接 respond_directly。"))
        response = model.invoke(messages)
        decision = self._parse_decision(response.content)
        if tool_call_count >= MAX_AGENT_TOOL_CALLS and decision.get("decision") == "use_tool":
            raise ValueError("Agent 超出最大工具调用次数后仍尝试继续调用工具")
        trace = list(state.get("trace") or [])
        trace.append(
            {
                "id": new_id(),
                "node": "agent_node",
                "decision": decision.get("decision"),
                "tool_name": decision.get("tool_name"),
                "summary": decision.get("reason") or "Agent 已完成本轮策略判断。",
                "status": "planned",
                "created_at": now_iso(),
            }
        )
        result = self._capture_direct_response(request, decision.get("result")) if decision.get("decision") == "respond_directly" else None
        agent_messages = [AIMessage(content=result.get("reply", ""))] if isinstance(result, dict) and result.get("reply") else []
        return {
            "decision": decision,
            "result": result,
            "agent_messages": agent_messages,
            "trace": trace,
        }

    def _tool_node(self, state: CreativeAgentState) -> dict[str, Any]:
        decision = state.get("decision") or {}
        request = state.get("request") or {}
        tool_name = str(decision.get("tool_name") or "").strip()
        tool_args = dict(decision.get("tool_args") or {}) if isinstance(decision.get("tool_args"), dict) else {}
        if tool_name == "suggest_painting_style" and request.get("session_id"):
            tool_args["session_id"] = request["session_id"]
        tool = self.tools.get(tool_name)
        if tool is None:
            raise ValueError(f"未知工具: {tool_name}")
        step = self._next_step()
        self._emit_stream_event(
            {
                "event": "tool_start",
                "data": {
                    "step": step,
                    "tool_name": tool_name,
                    "message": self._tool_start_message(tool_name),
                },
            }
        )
        started_at = time.perf_counter()
        tool_result = tool.invoke(tool_args)
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        self._emit_stream_event(
            {
                "event": "tool_done",
                "data": {
                    "step": step,
                    "tool_name": tool_name,
                    "message": self._tool_done_message(tool_name, tool_result),
                    "duration_ms": duration_ms,
                },
            }
        )
        trace = list(state.get("trace") or [])
        if trace:
            trace[-1] = {**trace[-1], "status": "completed"}
        trace.append(
            {
                "id": new_id(),
                "node": "tool_node",
                "decision": decision.get("decision"),
                "tool_name": tool_name,
                "summary": f"已执行工具 {tool_name}",
                "status": "completed",
                "created_at": now_iso(),
            }
        )
        captured_output = self._capture_tool_output(
            tool_name=tool_name,
            tool_result=tool_result,
            request=request,
        )
        updated_request = self._merge_result_into_request(request, tool_name, captured_output)
        return {
            "request": updated_request,
            "trace": trace,
            "tool_call_count": int(state.get("tool_call_count") or 0) + 1,
        }

    def _should_continue(self, state: CreativeAgentState) -> Literal["tool_node", "__end__"]:
        decision = state.get("decision") or {}
        if decision.get("decision") == "use_tool":
            return "tool_node"
        return END

    def _after_tool(self, state: CreativeAgentState) -> Literal["agent_node", "__end__"]:
        return "agent_node"

    def _build_system_prompt(self) -> str:
        return load_prompt("agent/creative_agent_system_prompt.txt")

    def _build_input_summary(self, request: dict[str, Any]) -> str:
        state = request.get("state") or {}
        attachments = request.get("attachments") or []
        style_payload = state.get("style_payload") or {}
        asset_candidates = state.get("asset_candidates") or {}
        allocation_plan = state.get("allocation_plan") or []
        last_tool_name = request.get("last_tool_name") or ""
        last_tool_result = request.get("last_tool_result") or {}
        selected_style_profile = request.get("selected_style_profile") or {}
        tool_history = request.get("tool_history") or []
        return (
            f"session_id={request.get('session_id')}\n"
            f"content_mode={request.get('content_mode') or state.get('content_mode') or ''}\n"
            f"stage={state.get('stage', 'initial_understanding')}\n"
            f"progress={state.get('progress')}\n"
            f"progress_label={state.get('progress_label') or ''}\n"
            f"action={request.get('action') or 'continue'}\n"
            f"text={request.get('text') or ''}\n"
            f"selected_items={request.get('selected_items') or []}\n"
            f"selected_style_profile={json.dumps(selected_style_profile, ensure_ascii=False)}\n"
            f"style_payload={json.dumps(style_payload, ensure_ascii=False)}\n"
            f"style_prompt={state.get('style_prompt') or ''}\n"
            f"asset_candidates={json.dumps(asset_candidates, ensure_ascii=False)}\n"
            f"allocation_plan={json.dumps(allocation_plan, ensure_ascii=False)}\n"
            f"image_count={state.get('image_count')}\n"
            f"draft_style_id={state.get('draft_style_id') or ''}\n"
            f"active_job_id={state.get('active_job_id') or ''}\n"
            f"last_tool_name={last_tool_name}\n"
            f"last_tool_result={json.dumps(last_tool_result, ensure_ascii=False)}\n"
            f"tool_history={json.dumps(tool_history, ensure_ascii=False)}\n"
            f"attachments={json.dumps(attachments, ensure_ascii=False)}\n"
            "请判断当前最合适的下一步，并优先使用工具推进创作流程。"
        )

    def _parse_decision(self, content: str) -> dict[str, Any]:
        text = str(content or "").strip()
        if text.startswith("```"):
            lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("Agent 输出缺少 JSON 决策对象")
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            raise ValueError("Agent 输出不是合法 JSON")
        if not isinstance(payload, dict):
            raise ValueError("Agent 决策结构非法")
        decision = str(payload.get("decision") or "").strip()
        if decision not in {"respond_directly", "use_tool"}:
            raise ValueError("Agent 决策类型非法")
        return payload

    def _capture_direct_response(self, request: dict[str, Any], result: Any) -> dict[str, Any]:
        state = request.get("state") or {}
        payload = result if isinstance(result, dict) else {}
        normalized_options = self._normalize_agent_options(payload.get("options"))
        normalized_result: dict[str, Any] = {
            "reply": str(payload.get("reply") or "").strip(),
            "stage": str(payload.get("stage") or state.get("stage") or "initial_understanding"),
            "locked": bool(payload.get("locked", state.get("locked"))),
            "options": normalized_options,
            "progress": self._normalize_progress(payload.get("progress"), fallback=state.get("progress")),
            "progress_label": str(payload.get("progress_label") or state.get("progress_label") or "").strip() or None,
            "active_job_id": str(payload.get("job_id") or payload.get("active_job_id") or state.get("active_job_id") or "").strip()
            or None,
        }
        for key in (
            "style_payload",
            "style_prompt",
            "image_count",
            "asset_candidates",
            "allocation_plan",
            "draft_style_id",
            "requirement_ready",
            "prompt_confirmable",
        ):
            if key in payload:
                normalized_result[key] = payload.get(key)
        return normalized_result

    def _capture_tool_output(
        self,
        *,
        tool_name: str,
        tool_result: Any,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name == "suggest_painting_style":
            tool_payload = tool_result if isinstance(tool_result, dict) else {}
            return {"style_guidance": tool_payload}
        if tool_name == "extract_assets":
            return {"asset_candidates": tool_result if isinstance(tool_result, dict) else {}}
        if tool_name == "generate_style_prompt":
            tool_payload = tool_result if isinstance(tool_result, dict) else {}
            return {
                "style_prompt": tool_payload.get("style_prompt"),
                "image_count": tool_payload.get("image_count"),
            }
        if tool_name == "allocate_assets_to_images":
            return {"allocation_plan": tool_result if isinstance(tool_result, list) else []}
        if tool_name == "save_style":
            tool_payload = tool_result if isinstance(tool_result, dict) else {}
            return {
                "style_saved": True,
                "saved_style_id": tool_payload.get("style_id"),
                "saved_style_name": tool_payload.get("style_name"),
            }
        if tool_name == "generate_images":
            tool_payload = tool_result if isinstance(tool_result, dict) else {}
            return {
                "active_job_id": tool_payload.get("job_id"),
                "job_status": tool_payload.get("status"),
            }
        if tool_name == "reset_progress":
            return tool_result if isinstance(tool_result, dict) else {}
        if tool_name == "generate_copy":
            tool_payload = tool_result if isinstance(tool_result, dict) else {}
            return {
                "copy_job_id": tool_payload.get("job_id"),
                "copy_job_status": tool_payload.get("status"),
            }
        return {}

    def _merge_result_into_request(self, request: dict[str, Any], tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
        state = dict(request.get("state") or {})
        for key in (
            "stage",
            "locked",
            "style_payload",
            "style_prompt",
            "image_count",
            "asset_candidates",
            "allocation_plan",
            "draft_style_id",
            "requirement_ready",
            "prompt_confirmable",
            "progress",
            "progress_label",
            "active_job_id",
            "style_guidance",
            "style_saved",
            "saved_style_id",
            "saved_style_name",
            "job_status",
            "copy_job_id",
            "copy_job_status",
        ):
            if key not in result:
                continue
            value = result.get(key)
            if value is None and key not in {"active_job_id", "saved_style_id", "saved_style_name", "copy_job_id"}:
                continue
            state[key] = value
        merged_request = {**request}
        merged_request["state"] = state
        tool_history = list(request.get("tool_history") or [])
        tool_history.append({"tool_name": tool_name, "captured_output": result})
        merged_request["tool_history"] = tool_history
        merged_request["last_tool_name"] = tool_name
        merged_request["last_tool_result"] = result
        return merged_request

    def _normalize_agent_options(self, raw_options: Any) -> dict[str, Any] | None:
        if raw_options is None:
            return None
        items = raw_options.get("items") if isinstance(raw_options, dict) else raw_options
        if not isinstance(items, list):
            raise ValueError("Agent 返回的 options 必须是列表或包含 items 的对象")
        normalized_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Agent option 必须是对象")
            label = str(item.get("label") or "").strip()
            if not label:
                raise ValueError("Agent option 缺少 label")
            action_hint = item.get("action_hint")
            normalized_items.append(
                {
                    "label": label,
                    "action_hint": str(action_hint).strip() if isinstance(action_hint, str) and action_hint.strip() else None,
                }
            )
        return {"items": normalized_items}

    def _normalize_progress(self, value: Any, *, fallback: Any) -> int | None:
        if isinstance(value, int):
            if 0 <= value <= 100:
                return value
            raise ValueError("Agent progress 必须在 0 到 100 之间")
        if isinstance(fallback, int) and 0 <= fallback <= 100:
            return fallback
        return None

    def _emit_stream_event(self, event: dict[str, Any]) -> None:
        stream_queue = getattr(self._stream_local, "event_queue", None)
        if stream_queue is None:
            return
        stream_queue.put(("sse", event))

    def _next_step(self) -> int:
        counter = getattr(self._stream_local, "step_counter", None)
        if counter is None:
            return 0
        counter["value"] += 1
        return int(counter["value"])

    def _thinking_message(self, request: dict[str, Any]) -> str:
        if request.get("last_tool_name"):
            return "正在组织下一步..."
        return "正在思考..."

    def _tool_start_message(self, tool_name: str) -> str:
        mapping = {
            "suggest_painting_style": "正在分析适合的绘画风格...",
            "extract_assets": "正在提取素材...",
            "generate_style_prompt": "正在生成提示词...",
            "allocate_assets_to_images": "正在规划分图方案...",
            "save_style": "正在保存风格...",
            "generate_images": "正在创建生成任务...",
            "reset_progress": "正在回退进度...",
            "generate_copy": "正在生成文案...",
        }
        return mapping.get(tool_name, f"正在执行 {tool_name}...")

    def _tool_done_message(self, tool_name: str, tool_result: Any) -> str:
        if tool_name == "suggest_painting_style":
            return "风格分析完成"
        if tool_name == "extract_assets":
            if isinstance(tool_result, dict):
                keywords = self._asset_summary_keywords(tool_result)
                if keywords:
                    return f"已提取素材：{'、'.join(keywords)}"
            return "已提取素材"
        if tool_name == "generate_style_prompt":
            return "提示词已就绪"
        if tool_name == "allocate_assets_to_images":
            plan = tool_result if isinstance(tool_result, list) else []
            count = len(plan)
            return f"已生成 {count} 张图的分配方案" if count else "已生成分图方案"
        if tool_name == "save_style":
            if isinstance(tool_result, dict):
                style_name = str(tool_result.get("style_name") or "").strip()
                if style_name:
                    return f"风格「{style_name}」已保存"
            return "风格已保存"
        if tool_name == "generate_images":
            return "图片生成任务已启动"
        if tool_name == "reset_progress":
            if isinstance(tool_result, dict):
                stage = str(tool_result.get("stage") or "").strip()
                if stage:
                    return f"已回退到{stage}阶段"
            return "已回退到指定阶段"
        if tool_name == "generate_copy":
            return "文案生成任务已启动"
        return f"{tool_name} 执行完成"

    def _asset_summary_keywords(self, tool_result: dict[str, Any]) -> list[str]:
        summary: list[str] = []
        for key in ("foods", "scenes", "locations", "keywords"):
            values = tool_result.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                text = str(value or "").strip()
                if text and text not in summary:
                    summary.append(text)
                if len(summary) >= 3:
                    return summary
        return summary
