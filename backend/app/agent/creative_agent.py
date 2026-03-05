from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict

from langchain_community.chat_message_histories import SQLChatMessageHistory
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.history import RunnableWithMessageHistory
from langgraph.graph import END, START, StateGraph

from backend.app.core.utils import new_id, now_iso


class CreativeAgentState(TypedDict, total=False):
    request: dict[str, Any]
    input_messages: list[BaseMessage]
    history_messages: list[BaseMessage]
    decision: dict[str, Any]
    result: dict[str, Any]
    agent_messages: list[BaseMessage]
    trace: list[dict[str, Any]]


@dataclass
class CreativeAgentTurn:
    reply: str
    stage: Literal["style_collecting", "prompt_revision", "asset_confirming", "locked"]
    options: dict[str, Any] | None = None
    style_payload: dict[str, Any] | None = None
    style_prompt: str | None = None
    image_count: int | None = None
    asset_candidates: dict[str, Any] | None = None
    allocation_plan: list[dict[str, Any]] = field(default_factory=list)
    locked: bool = False
    draft_style_id: str | None = None
    requirement_ready: bool | None = None
    prompt_confirmable: bool | None = None
    dynamic_stage: str | None = None
    dynamic_stage_label: str | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CreativeAgent:
    llm_provider: Any
    tools: dict[str, Any]
    db_path: Path

    def __post_init__(self) -> None:
        workflow = StateGraph(CreativeAgentState)
        workflow.add_node("agent_node", self._agent_node)
        workflow.add_node("tool_node", self._tool_node)
        workflow.add_edge(START, "agent_node")
        workflow.add_conditional_edges("agent_node", self._should_continue, ["tool_node", END])
        workflow.add_edge("tool_node", END)
        self._graph = workflow.compile()
        self._runnable = RunnableWithMessageHistory(
            RunnableLambda(self._invoke_graph),
            self._get_session_history,
            input_messages_key="input_messages",
            history_messages_key="history_messages",
            output_messages_key="agent_messages",
        )

    def respond(self, request: dict[str, Any]) -> CreativeAgentTurn:
        response = self._runnable.invoke(
            {
                "request": request,
                "input_messages": [HumanMessage(content=self._build_input_summary(request))],
            },
            config={"configurable": {"session_id": request["session_id"]}},
        )
        result = response.get("result") or {}
        return CreativeAgentTurn(
            reply=str(result.get("reply") or "").strip(),
            stage=result.get("stage", "style_collecting"),
            options=result.get("options"),
            style_payload=result.get("style_payload"),
            style_prompt=result.get("style_prompt"),
            image_count=result.get("image_count"),
            asset_candidates=result.get("asset_candidates"),
            allocation_plan=result.get("allocation_plan") or [],
            locked=bool(result.get("locked")),
            draft_style_id=result.get("draft_style_id"),
            requirement_ready=result.get("requirement_ready"),
            prompt_confirmable=result.get("prompt_confirmable"),
            dynamic_stage=result.get("dynamic_stage"),
            dynamic_stage_label=result.get("dynamic_stage_label"),
            trace=result.get("trace") or [],
        )

    def _invoke_graph(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._graph.invoke(payload)

    def _get_session_history(self, session_id: str) -> SQLChatMessageHistory:
        connection = f"sqlite:///{self.db_path.as_posix()}"
        return SQLChatMessageHistory(session_id=session_id, connection=connection)

    def _agent_node(self, state: CreativeAgentState) -> dict[str, Any]:
        model = self.llm_provider.build_chat_model()
        messages = [
            SystemMessage(content=self._build_system_prompt()),
            *state.get("history_messages", []),
            *state.get("input_messages", []),
        ]
        response = model.invoke(messages)
        decision = self._parse_decision(response.content)
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
        result = decision.get("result") if decision.get("decision") == "respond_directly" else None
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
        tool_args = decision.get("tool_args") if isinstance(decision.get("tool_args"), dict) else {}
        tool = self.tools.get(tool_name)
        if tool is None:
            raise ValueError(f"未知工具: {tool_name}")
        tool_result = tool.invoke(tool_args)
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
        result = self._build_turn_result(
            request=request,
            decision=decision,
            tool_name=tool_name,
            tool_result=tool_result,
            trace=trace,
        )
        return {
            "result": result,
            "agent_messages": [AIMessage(content=result.get("reply", ""))],
            "trace": trace,
        }

    def _should_continue(self, state: CreativeAgentState) -> Literal["tool_node", "__end__"]:
        decision = state.get("decision") or {}
        if decision.get("decision") == "use_tool":
            return "tool_node"
        return END

    def _build_system_prompt(self) -> str:
        return (
            "你是 Savory Canvas 的灵感创作 Agent。\n"
            "你必须在以下决策中二选一：\n"
            "1. respond_directly：无需工具即可直接给出兼容旧契约的结果。\n"
            "2. use_tool：选择一个工具继续处理。\n"
            "输出严格 JSON，结构为："
            '{"decision":"respond_directly|use_tool","reason":"","tool_name":"","tool_args":{},"dynamic_stage":"","dynamic_stage_label":"","result":null}。\n'
            "如果使用工具，tool_name 必须来自已知工具。\n"
            "如果直接回复，result 中必须包含 reply、stage、locked 字段。"
        )

    def _build_input_summary(self, request: dict[str, Any]) -> str:
        state = request.get("state") or {}
        attachments = request.get("attachments") or []
        return (
            f"session_id={request.get('session_id')}\n"
            f"stage={state.get('stage', 'style_collecting')}\n"
            f"action={request.get('action') or 'continue'}\n"
            f"text={request.get('text') or ''}\n"
            f"selected_items={request.get('selected_items') or []}\n"
            f"attachments={attachments}\n"
            "请判断当前最合适的下一步。"
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

    def _build_turn_result(
        self,
        *,
        request: dict[str, Any],
        decision: dict[str, Any],
        tool_name: str,
        tool_result: Any,
        trace: list[dict[str, Any]],
    ) -> dict[str, Any]:
        state = request.get("state") or {}
        dynamic_stage = decision.get("dynamic_stage")
        dynamic_stage_label = decision.get("dynamic_stage_label")
        if tool_name == "suggest_painting_style":
            tool_payload = tool_result if isinstance(tool_result, dict) else {}
            return {
                "reply": tool_payload.get("reply", ""),
                "stage": tool_payload.get("stage", state.get("stage", "style_collecting")),
                "locked": False,
                "options": tool_payload.get("options"),
                "dynamic_stage": dynamic_stage,
                "dynamic_stage_label": dynamic_stage_label,
                "trace": trace,
            }
        # TODO: 补齐 extract_assets / generate_style_prompt / allocate_assets_to_images /
        # generate_images / generate_copy 等工具的结果映射，当前骨架阶段先走兼容兜底。
        return {
            "reply": "Agent 已执行工具，但当前结果仍需要人工确认。",
            "stage": state.get("stage", "style_collecting"),
            "locked": bool(state.get("locked")),
            "dynamic_stage": dynamic_stage,
            "dynamic_stage_label": dynamic_stage_label,
            "trace": trace,
        }
