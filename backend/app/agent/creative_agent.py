from __future__ import annotations

import json
import queue
import re
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

from backend.app.core.errors import DomainError
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
            result["reply"] = self._sanitize_agent_reply_text(result["reply"].strip())
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
                    result["reply"] = self._sanitize_agent_reply_text(result["reply"].strip())
                decision = response.get("decision") or {}
                if "dynamic_stage" not in result and decision.get("dynamic_stage") is not None:
                    result["dynamic_stage"] = decision.get("dynamic_stage")
                if "dynamic_stage_label" not in result and decision.get("dynamic_stage_label") is not None:
                    result["dynamic_stage_label"] = decision.get("dynamic_stage_label")
                result["trace"] = response.get("trace") or []
                event_queue.put(("result", result))
            except DomainError as exc:
                event_queue.put(("error", {"code": exc.code, "message": exc.message}))
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
                if isinstance(payload, dict):
                    yield {"event": "error", "data": payload}
                else:
                    message = self._sanitize_stream_error_message(str(payload) or "Agent 执行异常")
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
        decision = self._resolve_deterministic_decision(request)
        reasoning_summaries: list[str] = []
        if decision is None:
            model = self.llm_provider.build_chat_model()
            messages = [
                SystemMessage(content=self._build_system_prompt()),
                *state.get("history_messages", []),
                HumanMessage(content=self._build_input_summary(request)),
            ]
            if tool_call_count >= MAX_AGENT_TOOL_CALLS:
                messages.insert(1, SystemMessage(content="你本轮已经达到最多 5 次工具调用限制，必须直接 respond_directly。"))
            response = model.invoke(messages)
            reasoning_summaries = self._extract_reasoning_summaries(response)
            for summary in reasoning_summaries:
                self._emit_stream_event(
                    {
                        "event": "thinking",
                        "data": {
                            "step": self._next_step(),
                            "message": summary,
                        },
                    }
                )
            decision = self._parse_decision(response.content)
        if not reasoning_summaries:
            decision_reason = str(decision.get("reason") or "").strip()
            if decision_reason:
                self._emit_stream_event(
                    {
                        "event": "thinking",
                        "data": {
                            "step": self._next_step(),
                            "message": decision_reason,
                        },
                    }
                )
        decision = self._guard_tool_decision(request, decision)
        if tool_call_count >= MAX_AGENT_TOOL_CALLS and decision.get("decision") == "use_tool":
            raise ValueError("Agent 超出最大工具调用次数后仍尝试继续调用工具")
        trace = list(state.get("trace") or [])
        trace.append(
            {
                "id": new_id(),
                "node": "agent_node",
                "decision": decision.get("decision"),
                "tool_name": decision.get("tool_name"),
                "summary": self._build_trace_summary(reasoning_summaries, decision),
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
        tool_args = self._enrich_tool_args(
            tool_name=tool_name,
            tool_args=decision.get("tool_args"),
            request=request,
        )
        blocked_result = self._guard_tool_preconditions(tool_name=tool_name, request=request)
        if blocked_result is not None:
            trace = list(state.get("trace") or [])
            if trace:
                trace[-1] = {**trace[-1], "status": "skipped", "summary": "前置条件未满足，已回到确认张数。"}
            updated_request = self._merge_result_into_request(request, tool_name, blocked_result)
            return {
                "request": updated_request,
                "trace": trace,
                "tool_call_count": int(state.get("tool_call_count") or 0) + 1,
            }
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
                "summary": self._tool_done_message(tool_name, tool_result),
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
        latest_assistant_reply = str(request.get("latest_assistant_reply") or "").strip()
        latest_assistant_options = request.get("latest_assistant_options") or []
        latest_assistant_stage = str(request.get("latest_assistant_stage") or "").strip()
        tool_history = request.get("tool_history") or []
        explicit_content_input_present = self._request_has_explicit_content_input(request)
        content_summary = self._summarize_asset_candidates(asset_candidates)
        style_profile_content_confirmation_needed = (
            bool(selected_style_profile)
            and str(request.get("action") or "").strip() == "use_style_profile"
            and not explicit_content_input_present
        )
        return (
            f"session_id={request.get('session_id')}\n"
            f"content_mode={request.get('content_mode') or state.get('content_mode') or ''}\n"
            f"stage={state.get('stage', 'initial_understanding')}\n"
            f"style_stage={state.get('style_stage') or ''}\n"
            f"progress={state.get('progress')}\n"
            f"progress_label={state.get('progress_label') or ''}\n"
            f"action={request.get('action') or 'continue'}\n"
            f"text={request.get('text') or ''}\n"
            f"selected_items={request.get('selected_items') or []}\n"
            f"selected_style_profile={json.dumps(selected_style_profile, ensure_ascii=False)}\n"
            f"latest_assistant_reply={latest_assistant_reply}\n"
            f"latest_assistant_options={json.dumps(latest_assistant_options, ensure_ascii=False)}\n"
            f"latest_assistant_stage={latest_assistant_stage}\n"
            f"explicit_content_input_present={explicit_content_input_present}\n"
            f"style_profile_content_confirmation_needed={style_profile_content_confirmation_needed}\n"
            f"content_summary={content_summary}\n"
            f"style_payload={json.dumps(style_payload, ensure_ascii=False)}\n"
            f"style_prompt={state.get('style_prompt') or ''}\n"
            f"asset_candidates={json.dumps(asset_candidates, ensure_ascii=False)}\n"
            f"allocation_plan={json.dumps(allocation_plan, ensure_ascii=False)}\n"
            f"image_count={state.get('image_count')}\n"
            f"draft_style_id={state.get('draft_style_id') or ''}\n"
            f"active_job_id={state.get('active_job_id') or ''}\n"
            f"prompt_confirmable={state.get('prompt_confirmable')}\n"
            f"requirement_ready={state.get('requirement_ready')}\n"
            f"image_count_confirmed={self._has_confirmed_image_count(state)}\n"
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
            "reply": self._sanitize_agent_reply_text(str(payload.get("reply") or "").strip()),
            "stage": str(payload.get("stage") or state.get("stage") or "initial_understanding"),
            "locked": bool(payload.get("locked", state.get("locked"))),
            "options": normalized_options,
            "progress": self._normalize_progress(payload.get("progress"), fallback=state.get("progress")),
            "progress_label": str(payload.get("progress_label") or state.get("progress_label") or "").strip() or None,
            "active_job_id": str(payload.get("job_id") or payload.get("active_job_id") or state.get("active_job_id") or "").strip()
            or None,
            "style_stage": str(payload.get("style_stage") or state.get("style_stage") or "painting_style"),
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
            "recommended_content_combos",
        ):
            if key in payload:
                normalized_result[key] = payload.get(key)
            elif key in state:
                normalized_result[key] = state.get(key)
        return normalized_result

    def _resolve_deterministic_decision(self, request: dict[str, Any]) -> dict[str, Any] | None:
        state = request.get("state") or {}
        action = str(request.get("action") or "").strip()
        last_tool_name = str(request.get("last_tool_name") or "").strip()
        last_tool_result = request.get("last_tool_result") if isinstance(request.get("last_tool_result"), dict) else {}

        if last_tool_name == "generate_images":
            active_job_id = str(
                last_tool_result.get("active_job_id")
                or last_tool_result.get("job_id")
                or state.get("active_job_id")
                or ""
            ).strip()
            if active_job_id:
                return {
                    "decision": "respond_directly",
                    "reason": "生成任务已创建，直接同步任务状态。",
                    "result": {
                        "reply": "生成任务已经启动了，我会继续帮你同步出图进度。",
                        "stage": "generation_started",
                        "locked": False,
                        "progress": 85,
                        "progress_label": "插画生成中...",
                        "active_job_id": active_job_id,
                    },
                }

        if self._is_image_count_action(action):
            allocation_plan = state.get("allocation_plan")
            if isinstance(allocation_plan, list) and allocation_plan:
                return {
                    "decision": "respond_directly",
                    "reason": "分图规划已经生成，直接请用户确认方案。",
                    "result": {
                        "reply": "我已经按这次确认的张数把分图方案整理好了，你看看这版安排是否合适；如果没问题，我们就可以直接开始出图。",
                        "stage": "allocation_confirmation",
                        "locked": False,
                        "progress": 70,
                        "progress_label": "分图规划已完成，待确认",
                        "allocation_plan": allocation_plan,
                    },
                }
            asset_candidates = state.get("asset_candidates")
            if isinstance(asset_candidates, dict) and asset_candidates:
                return {
                    "decision": "use_tool",
                    "reason": "用户已确认图片张数，直接进入分图规划。",
                    "tool_name": "allocate_assets_to_images",
                    "tool_args": {},
                }
            return {
                "decision": "respond_directly",
                "reason": "当前还没有足够素材，先补充内容后再确认张数。",
                "result": {
                    "reply": "我这边还缺少足够的内容素材，先把地点或景点补充完整，我再继续帮你确认张数并分图。",
                    "stage": "content_confirmation",
                    "locked": False,
                    "progress": 35,
                    "progress_label": "内容待补充",
                },
            }

        if action != "confirm_and_generate":
            return None

        active_job_id = str(state.get("active_job_id") or "").strip()
        if active_job_id:
            return {
                "decision": "respond_directly",
                "reason": "当前已经存在进行中的生成任务，无需重复触发。",
                "result": {
                    "reply": "生成任务已经在进行中了，我会继续帮你同步最新进度。",
                    "stage": "generation_started",
                    "locked": False,
                    "progress": 85,
                    "progress_label": "插画生成中...",
                    "active_job_id": active_job_id,
                },
            }

        allocation_plan = state.get("allocation_plan")
        if isinstance(allocation_plan, list) and allocation_plan and self._has_confirmed_image_count(state):
            return {
                "decision": "use_tool",
                "reason": "用户已确认开始出图，直接创建生成任务。",
                "tool_name": "generate_images",
                "tool_args": {},
            }

        asset_candidates = state.get("asset_candidates")
        if isinstance(asset_candidates, dict) and asset_candidates and self._has_confirmed_image_count(state):
            return {
                "decision": "use_tool",
                "reason": "当前缺少持久化分图方案，先自动补齐分图后继续生成。",
                "tool_name": "allocate_assets_to_images",
                "tool_args": {},
            }

        return {
            "decision": "respond_directly",
            "reason": "当前还没有可执行的分图方案，先回到分图确认。",
            "result": {
                "reply": "我这边还没有拿到可直接出图的分图方案，先把分图确认好，我再立刻开始生成。",
                "stage": "allocation_confirmation",
                "locked": False,
                "progress": 75,
                "progress_label": "分图规划完成",
            },
        }

    def _capture_tool_output(
        self,
        *,
        tool_name: str,
        tool_result: Any,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name == "suggest_painting_style":
            tool_payload = tool_result if isinstance(tool_result, dict) else {}
            return {
                "style_guidance": tool_payload,
                "style_stage": str(tool_payload.get("stage") or request.get("state", {}).get("style_stage") or "painting_style"),
            }
        if tool_name == "extract_assets":
            return {"asset_candidates": tool_result if isinstance(tool_result, dict) else {}}
        if tool_name == "recommend_city_content_combos":
            tool_payload = tool_result if isinstance(tool_result, dict) else {}
            return {"recommended_content_combos": tool_payload.get("items") if isinstance(tool_payload.get("items"), list) else []}
        if tool_name == "generate_style_prompt":
            tool_payload = tool_result if isinstance(tool_result, dict) else {}
            captured = {
                "style_prompt": tool_payload.get("style_prompt"),
                "image_count": tool_payload.get("image_count"),
            }
            if not self._has_confirmed_image_count(captured):
                captured.update(
                    {
                        "stage": "count_confirmation_required",
                        "progress": 45,
                        "progress_label": "确认张数",
                        "prompt_confirmable": False,
                    }
                )
            else:
                captured["prompt_confirmable"] = True
            return captured
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
            "style_stage",
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

    def _sanitize_agent_reply_text(self, reply: str) -> str:
        sanitized = str(reply or "").strip()
        replacements = {
            "我已经接住了": "我已经确认了",
            "已经接住了": "已经确定了",
            "这套风格我已经接住": "这套风格已经确定",
            "这个风格我已经接住": "这个风格已经确定",
            "风格我已经接住": "风格已经确定",
            "接住了": "确定了",
            "接住": "确认",
        }
        for source, target in replacements.items():
            sanitized = sanitized.replace(source, target)
        return sanitized

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

    def _has_confirmed_image_count(self, state: dict[str, Any]) -> bool:
        image_count = state.get("image_count")
        return isinstance(image_count, int) and 1 <= image_count <= 10

    def _has_asset_candidates(self, state: dict[str, Any]) -> bool:
        candidates = state.get("asset_candidates")
        if not isinstance(candidates, dict):
            return False
        for key in ("locations", "foods", "scenes", "keywords"):
            values = candidates.get(key)
            if isinstance(values, list) and any(str(item).strip() for item in values):
                return True
        return False

    def _is_image_count_action(self, action: Any) -> bool:
        normalized = str(action or "").strip().lower()
        return bool(re.fullmatch(r"(?:confirm|select|set)_image_count_\d+", normalized))

    def _request_has_explicit_content_input(self, request: dict[str, Any]) -> bool:
        if str(request.get("text") or "").strip():
            return True
        attachments = request.get("attachments")
        if not isinstance(attachments, list):
            return False
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            usage_type = str(attachment.get("usage_type") or "").strip()
            attachment_type = str(attachment.get("type") or "").strip()
            if usage_type == "content_asset":
                return True
            if attachment_type in {"video", "text", "transcript"}:
                return True
        return False

    def _summarize_asset_candidates(self, asset_candidates: Any) -> str:
        if not isinstance(asset_candidates, dict):
            return ""
        locations = self._string_list(asset_candidates.get("locations"))
        scenes = self._string_list(asset_candidates.get("scenes"))
        foods = self._string_list(asset_candidates.get("foods"))
        keywords = self._string_list(asset_candidates.get("keywords"))
        parts: list[str] = []
        if locations:
            parts.append(f"地点 {locations}")
        if scenes:
            parts.append(f"景点 {scenes}")
        if foods:
            parts.append(f"美食 {foods}")
        if keywords:
            parts.append(f"关键词 {keywords}")
        return "；".join(parts[:4])

    def _string_list(self, values: Any) -> str:
        if not isinstance(values, list):
            return ""
        normalized = [str(value).strip() for value in values if str(value).strip()]
        if not normalized:
            return ""
        return "、".join(normalized[:3])

    def _guard_tool_decision(self, request: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
        if decision.get("decision") != "use_tool":
            return decision
        state = request.get("state") or {}
        action = str(request.get("action") or "").strip()
        tool_name = str(decision.get("tool_name") or "").strip()
        if action == "recommend_city_combo" and tool_name != "recommend_city_content_combos":
            return {
                "decision": "use_tool",
                "reason": "用户明确想先看候选城市内容组合，必须优先调用推荐组合工具。",
                "tool_name": "recommend_city_content_combos",
                "tool_args": {"limit": 2},
            }
        if tool_name == "extract_assets":
            has_asset_candidates = self._has_asset_candidates(state)
            explicit_content_input_present = self._request_has_explicit_content_input(request)
            if has_asset_candidates and not explicit_content_input_present:
                if str(state.get("style_prompt") or "").strip():
                    if self._has_confirmed_image_count(state):
                        return {
                            "decision": "use_tool",
                            "reason": "已有素材且张数已确认，直接进入分图规划。",
                            "tool_name": "allocate_assets_to_images",
                            "tool_args": {},
                        }
                    return {
                        "decision": "respond_directly",
                        "reason": "已有素材与提示词，但张数尚未确认，先继续确认张数。",
                        "result": {
                            "reply": "我已经把提示词整理好了，不过还差最后一步：请先确认这次要生成几张图，我再继续帮你分配每张图的重点内容。",
                            "stage": "count_confirmation_required",
                            "locked": False,
                            "progress": self._normalize_progress(state.get("progress"), fallback=45) or 45,
                            "progress_label": "确认张数",
                            "prompt_confirmable": False,
                        },
                    }
                return {
                    "decision": "use_tool",
                    "reason": "当前素材已经提取过，且本轮没有新的内容输入，无需重复提取，直接整理提示词。",
                    "tool_name": "generate_style_prompt",
                    "tool_args": {},
                }
            if has_asset_candidates and self._is_image_count_action(request.get("action")):
                if str(state.get("style_prompt") or "").strip():
                    if self._has_confirmed_image_count(state):
                        return {
                            "decision": "use_tool",
                            "reason": "已有素材且张数已确认，直接进入分图规划。",
                            "tool_name": "allocate_assets_to_images",
                            "tool_args": {},
                        }
                    return {
                        "decision": "respond_directly",
                        "reason": "已有素材与提示词，但张数尚未确认，先继续确认张数。",
                        "result": {
                            "reply": "我已经把提示词整理好了，不过还差最后一步：请先确认这次要生成几张图，我再继续帮你分配每张图的重点内容。",
                            "stage": "count_confirmation_required",
                            "locked": False,
                            "progress": self._normalize_progress(state.get("progress"), fallback=45) or 45,
                            "progress_label": "确认张数",
                            "prompt_confirmable": False,
                        },
                    }
                return {
                    "decision": "use_tool",
                    "reason": "已有素材，无需重复提取，直接整理提示词。",
                    "tool_name": "generate_style_prompt",
                    "tool_args": {},
                }
            return decision
        if tool_name != "allocate_assets_to_images":
            return decision
        if self._has_confirmed_image_count(state):
            return decision
        progress = self._normalize_progress(state.get("progress"), fallback=45) or 45
        return {
            "decision": "respond_directly",
            "reason": "当前还没有确认生成张数，先继续确认张数。",
            "result": {
                "reply": "我已经把提示词整理好了，不过还差最后一步：请先确认这次要生成几张图，我再继续帮你分配每张图的重点内容。",
                "stage": "count_confirmation_required",
                "locked": False,
                "progress": progress,
                "progress_label": "确认张数",
                "prompt_confirmable": False,
            },
        }

    def _guard_tool_preconditions(self, *, tool_name: str, request: dict[str, Any]) -> dict[str, Any] | None:
        if tool_name != "allocate_assets_to_images":
            return None
        state = request.get("state") or {}
        if self._has_confirmed_image_count(state):
            return None
        progress = self._normalize_progress(state.get("progress"), fallback=45) or 45
        return {
            "stage": "count_confirmation_required",
            "progress": progress,
            "progress_label": "确认张数",
            "prompt_confirmable": False,
            "allocation_blocked_reason": "missing_image_count",
        }

    def _enrich_tool_args(self, *, tool_name: str, tool_args: Any, request: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(tool_args or {}) if isinstance(tool_args, dict) else {}
        request_state = request.get("state") or {}
        session_id = str(request.get("session_id") or "").strip()
        request_text = str(request.get("text") or "")
        selected_items = request.get("selected_items")
        normalized_items = selected_items if isinstance(selected_items, list) else []

        if tool_name == "suggest_painting_style" and session_id:
            enriched["session_id"] = session_id
            enriched["user_reply"] = request_text
            enriched["selected_items"] = normalized_items
            enriched["stage"] = (
                str(enriched.get("stage") or "").strip()
                or str(request_state.get("style_stage") or "").strip()
                or "painting_style"
            )
        elif tool_name == "extract_assets" and session_id:
            enriched["session_id"] = session_id
            enriched["user_hint"] = request_text
            enriched["style_prompt"] = str(request_state.get("style_prompt") or "")
        elif tool_name == "recommend_city_content_combos" and session_id:
            enriched["session_id"] = session_id
            enriched["limit"] = int(enriched.get("limit") or 2)
        elif tool_name == "generate_style_prompt" and session_id:
            enriched["session_id"] = session_id
            enriched["feedback"] = request_text
        elif tool_name == "allocate_assets_to_images" and session_id:
            enriched["session_id"] = session_id
            enriched["user_hint"] = request_text
        elif tool_name == "generate_images" and session_id:
            enriched["session_id"] = session_id
            enriched["draft_state"] = {
                "style_payload": request_state.get("style_payload") if isinstance(request_state.get("style_payload"), dict) else {},
                "style_prompt": str(request_state.get("style_prompt") or ""),
                "image_count": request_state.get("image_count"),
                "allocation_plan": request_state.get("allocation_plan") if isinstance(request_state.get("allocation_plan"), list) else [],
                "draft_style_id": request_state.get("draft_style_id"),
            }
        elif tool_name in {"save_style", "reset_progress"} and session_id:
            enriched["session_id"] = session_id
        elif tool_name == "generate_copy":
            active_job_id = str(request_state.get("active_job_id") or "").strip()
            if active_job_id:
                enriched["job_id"] = active_job_id

        return enriched

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

    def _extract_reasoning_summaries(self, response: Any) -> list[str]:
        raw_value = getattr(response, "reasoning_summaries", None)
        if not isinstance(raw_value, list):
            return []
        summaries: list[str] = []
        seen: set[str] = set()
        for item in raw_value:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            summaries.append(text)
        return summaries

    def _build_trace_summary(self, reasoning_summaries: list[str], decision: dict[str, Any]) -> str:
        if reasoning_summaries:
            return "\n\n".join(reasoning_summaries)
        return str(decision.get("reason") or "Agent 已完成本轮策略判断。")

    def _sanitize_stream_error_message(self, message: str) -> str:
        normalized = " ".join(str(message or "").split())
        lowered = normalized.lower()
        if not normalized:
            return "Agent 执行异常"
        if any(marker in lowered for marker in ("<!doctype", "<html", "cloudflare", "bad gateway", "gateway", "502", "503", "504")):
            return "模型服务暂时不可用，请稍后重试"
        if len(normalized) > 240:
            return "模型服务暂时不可用，请稍后重试"
        return normalized

    def _tool_start_message(self, tool_name: str) -> str:
        mapping = {
            "suggest_painting_style": "正在分析适合的绘画风格...",
            "extract_assets": "正在提取素材...",
            "recommend_city_content_combos": "正在整理两套城市内容组合...",
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
        if tool_name == "recommend_city_content_combos":
            items = tool_result.get("items") if isinstance(tool_result, dict) else []
            count = len(items) if isinstance(items, list) else 0
            return f"已整理 {count} 套城市内容组合" if count else "城市内容组合已准备好"
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
