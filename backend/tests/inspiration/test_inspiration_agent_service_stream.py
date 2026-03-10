from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from conftest import create_session, create_style, setup_model_routing


@dataclass
class _FakeChatResponse:
    content: str
    reasoning_summaries: list[str] = field(default_factory=list)


class _FakeChatModel:
    def __init__(self, decisions: list[dict[str, Any]]):
        self._decisions = list(decisions)
        self.calls: list[list[Any]] = []

    def invoke(self, messages: list[Any]) -> _FakeChatResponse:
        self.calls.append(messages)
        index = len(self.calls) - 1
        payload = dict(self._decisions[index])
        reasoning_summaries = payload.pop("__reasoning_summaries", [])
        return _FakeChatResponse(
            content=json.dumps(payload, ensure_ascii=False),
            reasoning_summaries=list(reasoning_summaries) if isinstance(reasoning_summaries, list) else [],
        )


class _FakeLLMProvider:
    def __init__(self, decisions: list[dict[str, Any]]):
        self.model = _FakeChatModel(decisions)

    def build_chat_model(self) -> _FakeChatModel:
        return self.model


class _ToolStub:
    def __init__(self, fn):
        self._fn = fn

    def invoke(self, args: dict[str, Any]) -> Any:
        return self._fn(args)


def _parse_sse_body(body: str) -> list[dict[str, Any]]:
    normalized = body.replace("\r\n", "\n")
    events: list[dict[str, Any]] = []
    for chunk in normalized.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = chunk.split("\n")
        event_type = ""
        data = ""
        for line in lines:
            if line.startswith("event:"):
                event_type = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
        events.append({"event": event_type, "data": json.loads(data) if data else None})
    return events

def test_build_creative_tools_supports_reset_progress_save_style_and_session_generation():
    from backend.app.agent.tools import build_creative_tools

    calls: list[tuple[str, str, str | None]] = []

    class RuntimeStub:
        def save_style_from_agent(self, session_id: str) -> dict[str, Any]:
            calls.append(("save_style_from_agent", session_id, None))
            return {"style_id": "style-1", "status": "saved"}

        def generate_images(self, *, session_id: str) -> dict[str, Any]:
            calls.append(("generate_images", session_id, None))
            return {"job_id": "job-1", "status": "queued"}

        def reset_progress(self, *, session_id: str, reset_to: str) -> dict[str, Any]:
            calls.append(("reset_progress", session_id, reset_to))
            return {"stage": "prompt_reopened"}

    tools = build_creative_tools(RuntimeStub())
    save_result = tools["save_style"].invoke({"session_id": "session-1"})
    image_result = tools["generate_images"].invoke({"session_id": "session-1"})
    reset_result = tools["reset_progress"].invoke({"session_id": "session-1", "reset_to": "prompt"})

    assert save_result["style_id"] == "style-1"
    assert image_result["job_id"] == "job-1"
    assert reset_result["stage"] == "prompt_reopened"
    assert calls == [
        ("save_style_from_agent", "session-1", None),
        ("generate_images", "session-1", None),
        ("reset_progress", "session-1", "prompt"),
    ]


def test_inspiration_service_save_style_from_agent_creates_style(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])
    state["stage"] = "locked_ready"
    state["locked"] = True
    state["style_prompt"] = "生成一张：西安城墙与泡馍攻略图。"
    state["style_payload"] = {
        "painting_style": "复古手账",
        "color_mood": "暖黄",
        "prompt_example": "统一复古手账质感。",
        "style_prompt": "生成一张：西安城墙与泡馍攻略图。",
        "extra_keywords": ["贴纸标注"],
    }
    service.inspiration_repo.upsert_state(state)

    monkeypatch.setattr(
        service,
        "_summarize_style_for_save",
        lambda **_: (
            "西安复古手账",
            {
                "painting_style": "复古手账",
                "color_mood": "暖黄",
                "prompt_example": "统一复古手账质感。",
                "style_prompt": "生成一张：西安城墙与泡馍攻略图。",
                "extra_keywords": ["贴纸标注"],
                "sample_image_asset_ids": [],
            },
        ),
    )

    result = service.save_style_from_agent(session["id"])

    assert result["status"] == "saved"
    assert result["style_name"] == "西安复古手账"
    saved_style = service.style_repo.get(result["style_id"])
    assert saved_style is not None
    assert saved_style["name"] == "西安复古手账"
    assert saved_style["session_id"] is None


def test_inspiration_service_generate_images_can_auto_start_when_ready_without_locked(client, monkeypatch):
    session = create_session(client, content_mode="food_scenic")
    style = create_style(client, session["id"], {"painting_style": "自然写实"})
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])
    state["stage"] = "allocation_ready"
    state["locked"] = False
    state["draft_style_id"] = style["id"]
    state["image_count"] = 2
    state["allocation_plan"] = [{"slot_index": 1, "focus_title": "城墙主图", "focus_description": "突出城墙。"}]
    service.inspiration_repo.upsert_state(state)

    scheduled_job_ids: list[str] = []
    monkeypatch.setattr(service.generation_worker, "schedule", lambda job_id: scheduled_job_ids.append(job_id))

    result = service.generate_images(session_id=session["id"])

    assert result["status"] == "queued"
    assert scheduled_job_ids == [result["job_id"]]
    job = service.generation_worker.job_repo.get(result["job_id"])
    assert job is not None
    assert job["session_id"] == session["id"]
    assert job["style_profile_id"] == style["id"]
    assert job["image_count"] == 2


def test_generate_images_prevents_duplicate_job(client, monkeypatch):
    session = create_session(client, content_mode="food_scenic")
    style = create_style(client, session["id"], {"painting_style": "自然写实"})
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])
    state["stage"] = "allocation_ready"
    state["image_count"] = 2
    state["allocation_plan"] = [{"slot_index": 1, "focus_title": "城墙主图", "focus_description": "突出城墙。"}]
    state["draft_style_id"] = style["id"]
    service.inspiration_repo.upsert_state(state)

    scheduled_job_ids: list[str] = []
    monkeypatch.setattr(service.generation_worker, "schedule", lambda job_id: scheduled_job_ids.append(job_id))

    first_result = service.generate_images(session_id=session["id"])
    second_result = service.generate_images(session_id=session["id"])

    assert first_result["job_id"] == second_result["job_id"]
    assert second_result["already_running"] is True
    assert len(scheduled_job_ids) == 1


def test_inspiration_service_reset_progress_clears_state_by_scope(client):
    session = create_session(client)
    style = create_style(client, session["id"], {"painting_style": "复古手账"})
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])
    state.update(
        {
            "stage": "allocation_ready",
            "locked": True,
            "style_payload": {"painting_style": "复古手账", "color_mood": "暖黄", "prompt_example": "统一风格", "style_prompt": "图解西安", "extra_keywords": []},
            "style_prompt": "图解西安",
            "asset_candidates": {"foods": ["羊肉泡馍"]},
            "allocation_plan": [{"slot_index": 1, "focus_title": "城墙主图"}],
            "draft_style_id": style["id"],
            "progress": 78,
            "progress_label": "分图规划",
            "active_job_id": "job-123",
        }
    )
    service.inspiration_repo.upsert_state(state)

    result = service.reset_progress(session_id=session["id"], reset_to="allocation")

    assert result["stage"] == "prompt_confirmed"
    updated = service._ensure_state(session["id"])
    assert updated["allocation_plan"] == []
    assert updated["locked"] is False
    assert updated["active_job_id"] is None
    assert updated["style_prompt"] == "图解西安"
    assert updated["style_payload"]["painting_style"] == "复古手账"


def test_inspiration_send_message_propagates_agent_error_without_fallback(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration

    monkeypatch.setattr(service, "_run_agent_turn", lambda **_: (_ for _ in ()).throw(RuntimeError("agent boom")))

    with pytest.raises(RuntimeError, match="agent boom"):
        asyncio.run(
            service.send_message(
                session_id=session["id"],
                text="请继续",
                selected_items=[],
                action=None,
                image_usages=[],
                images=[],
                videos=[],
            )
        )


def test_inspiration_progress_can_move_from_zero_to_hundred(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration

    progress_turns = iter(
        [
            {
                "reply": "我先了解一下你这次想做的方向 🙂",
                "stage": "briefing",
                "locked": False,
                "progress": 10,
                "progress_label": "初始了解",
                "options": {"items": [{"label": "我想做西安美食攻略", "action_hint": "describe_goal"}]},
                "trace": [],
            },
            {
                "reply": "风格已经对齐好了，我来继续整理提示词。",
                "stage": "style_aligned",
                "locked": False,
                "progress": 35,
                "progress_label": "风格确定",
                "options": {"items": [{"label": "继续整理提示词", "action_hint": "continue_prompt"}]},
                "trace": [],
            },
            {
                "reply": "分图已经排好了，我准备直接启动生成。",
                "stage": "allocation_ready",
                "locked": True,
                "progress": 78,
                "progress_label": "分图规划",
                "options": {"items": [{"label": "直接开始生成", "action_hint": "auto_generate"}]},
                "trace": [],
            },
            {
                "reply": "我已经帮你启动生成啦 🎨",
                "stage": "generation_started",
                "locked": True,
                "progress": 100,
                "progress_label": "生成已启动",
                "active_job_id": "job-123",
                "options": {"items": [{"label": "回到分图再调一下", "action_hint": "revise"}]},
                "trace": [],
            },
        ]
    )

    monkeypatch.setattr(service, "_run_agent_turn", lambda **_: next(progress_turns))

    progress_values: list[int] = []
    for text in ["先聊聊方向", "这个风格挺好", "分图也可以", "那就开始生成"]:
        response = client.post("/api/v1/inspirations/messages", data={"session_id": session["id"], "text": text})
        assert response.status_code == 200
        progress_values.append(response.json()["draft"]["progress"])

    assert progress_values == [10, 35, 78, 100]


def test_stream_endpoint_returns_sse_events(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration

    monkeypatch.setattr(
        service.creative_agent,
        "respond_stream",
        lambda payload: iter(
            [
                {"event": "thinking", "data": {"step": 1, "message": "正在思考..."}},
                {"event": "tool_start", "data": {"step": 2, "tool_name": "extract_assets", "message": "正在提取素材..."}},
                {"event": "tool_done", "data": {"step": 2, "tool_name": "extract_assets", "message": "已提取素材：羊肉泡馍", "duration_ms": 12}},
                {
                    "event": "result",
                    "data": {
                        "reply": "我已经把素材整理好了。",
                        "stage": "asset_ready",
                        "locked": False,
                        "progress": 52,
                        "progress_label": "素材已整理",
                        "options": {"items": [{"label": "继续分图", "action_hint": "continue_allocation"}]},
                    },
                },
            ]
        ),
    )

    response = client.post("/api/v1/inspirations/messages/stream", data={"session_id": session["id"], "text": "西安攻略"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse_body(response.text)
    assert [event["event"] for event in events] == ["thinking", "tool_start", "tool_done", "done"]


def test_stream_emits_thinking_before_tool(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration

    monkeypatch.setattr(
        service.creative_agent,
        "respond_stream",
        lambda payload: iter(
            [
                {"event": "thinking", "data": {"step": 1, "message": "正在思考..."}},
                {"event": "tool_start", "data": {"step": 2, "tool_name": "generate_style_prompt", "message": "正在生成提示词..."}},
                {"event": "tool_done", "data": {"step": 2, "tool_name": "generate_style_prompt", "message": "提示词已就绪", "duration_ms": 10}},
                {"event": "result", "data": {"reply": "提示词我已经整理好了。", "stage": "prompt_ready", "locked": False}},
            ]
        ),
    )

    response = client.post("/api/v1/inspirations/messages/stream", data={"session_id": session["id"], "text": "继续"})
    events = _parse_sse_body(response.text)

    assert events[0]["event"] == "thinking"
    assert events[1]["event"] == "tool_start"
    assert events[2]["event"] == "tool_done"


def test_stream_done_contains_full_response(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration

    monkeypatch.setattr(
        service.creative_agent,
        "respond_stream",
        lambda payload: iter(
            [
                {
                    "event": "result",
                    "data": {
                        "reply": "我已经帮你整理好下一步方向啦。",
                        "stage": "style_ready",
                        "locked": False,
                        "progress": 30,
                        "progress_label": "风格确定",
                        "options": {"items": [{"label": "继续提取素材", "action_hint": "extract_assets"}]},
                    },
                }
            ]
        ),
    )

    response = client.post("/api/v1/inspirations/messages/stream", data={"session_id": session["id"], "text": "想做西安攻略"})
    events = _parse_sse_body(response.text)

    assert events[-1]["event"] == "done"
    done_payload = events[-1]["data"]
    assert done_payload["session_id"] == session["id"]
    assert done_payload["draft"]["stage"] == "style_ready"
    assert done_payload["draft"]["progress"] == 30
    assert done_payload["messages"][-1]["content"] == "我已经帮你整理好下一步方向啦。"


def test_stream_error_on_agent_failure(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration

    def raising_stream(payload):
        raise RuntimeError("agent stream boom")
        yield  # pragma: no cover

    monkeypatch.setattr(service.creative_agent, "respond_stream", raising_stream)

    response = client.post("/api/v1/inspirations/messages/stream", data={"session_id": session["id"], "text": "继续"})
    events = _parse_sse_body(response.text)

    assert events[-1]["event"] == "error"
    assert events[-1]["data"]["code"] == "E-1099"


def test_stream_error_message_is_sanitized_for_html_upstream(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration

    monkeypatch.setattr(
        service.creative_agent,
        "respond_stream",
        lambda payload: iter(
            [
                {
                    "event": "error",
                    "data": {
                        "code": "E-1099",
                        "message": "<!DOCTYPE html><html><body><h1>502 Bad Gateway</h1><p>cloudflare</p></body></html>",
                    },
                }
            ]
        ),
    )

    response = client.post("/api/v1/inspirations/messages/stream", data={"session_id": session["id"], "text": "继续"})
    events = _parse_sse_body(response.text)

    assert events[-1]["event"] == "error"
    assert events[-1]["data"]["message"] == "模型服务暂时不可用，请稍后重试"


def test_stream_suggest_painting_style_uses_request_session_id(client, monkeypatch, tmp_path):
    from backend.app.agent.creative_agent import CreativeAgent
    from backend.app.agent.tools import build_creative_tools

    session = create_session(client)
    service = client.app.state.services.inspiration
    observed_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        service.style_service,
        "chat",
        lambda *, session_id, stage, user_reply, selected_items: (
            observed_calls.append(
                {
                    "session_id": session_id,
                    "stage": stage,
                    "user_reply": user_reply,
                    "selected_items": list(selected_items),
                }
            )
            or {
                "reply": "我先帮你梳理风格方向。",
                "options": {"title": "请选择", "items": ["自然写实"], "max": 1},
                "stage": stage,
                "next_stage": stage,
                "is_finished": False,
                "fallback_used": False,
            }
        ),
    )

    agent = CreativeAgent(
        llm_provider=_FakeLLMProvider(
            [
                {
                    "decision": "use_tool",
                    "reason": "先给出风格建议。",
                    "tool_name": "suggest_painting_style",
                    "tool_args": {
                        "session_id": "wrong-session",
                    },
                },
                {
                    "decision": "respond_directly",
                    "reason": "风格建议已经完成。",
                    "result": {
                        "reply": "我已经拿到风格建议了。",
                        "stage": "style_ready",
                        "locked": False,
                        "progress": 22,
                        "progress_label": "风格分析",
                        "options": {"items": [{"label": "继续", "action_hint": "continue"}]},
                    },
                },
            ]
        ),
        tools=build_creative_tools(service),
        db_path=tmp_path / "stream-agent.db",
    )
    monkeypatch.setattr(service, "creative_agent", agent)

    response = client.post("/api/v1/inspirations/messages/stream", data={"session_id": session["id"], "text": "我想做西安攻略"})

    assert response.status_code == 200
    events = _parse_sse_body(response.text)
    assert events[0]["event"] == "thinking"
    assert observed_calls == [
        {
            "session_id": session["id"],
            "stage": "painting_style",
            "user_reply": "我想做西安攻略",
            "selected_items": [],
        }
    ]


def test_stream_extract_assets_fills_missing_args_from_request(client, monkeypatch, tmp_path):
    from backend.app.agent.creative_agent import CreativeAgent
    from backend.app.agent.tools import build_creative_tools

    session = create_session(client)
    service = client.app.state.services.inspiration
    observed_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        service,
        "extract_assets",
        lambda *, session_id, user_hint, style_prompt: (
            observed_calls.append(
                {
                    "session_id": session_id,
                    "user_hint": user_hint,
                    "style_prompt": style_prompt,
                }
            )
            or {"locations": ["西安"], "keywords": ["探店"]}
        ),
    )

    agent = CreativeAgent(
        llm_provider=_FakeLLMProvider(
            [
                {
                    "decision": "use_tool",
                    "reason": "先提取素材。",
                    "tool_name": "extract_assets",
                    "tool_args": {
                        "session_id": "wrong-session",
                    },
                },
                {
                    "decision": "respond_directly",
                    "reason": "素材提取完成。",
                    "result": {
                        "reply": "我已经提取好了西安探店相关素材。",
                        "stage": "asset_ready",
                        "locked": False,
                        "progress": 45,
                        "progress_label": "素材提取",
                        "options": {"items": [{"label": "继续整理分图", "action_hint": "continue"}]},
                    },
                },
            ]
        ),
        tools=build_creative_tools(service),
        db_path=tmp_path / "extract-stream-agent.db",
    )
    monkeypatch.setattr(service, "creative_agent", agent)

    response = client.post(
        "/api/v1/inspirations/messages/stream",
        data={"session_id": session["id"], "text": "帮我整理一个西安探店图文思路"},
    )

    assert response.status_code == 200
    events = _parse_sse_body(response.text)
    assert [event["event"] for event in events] == ["thinking", "thinking", "tool_start", "tool_done", "thinking", "thinking", "done"]
    assert observed_calls == [
        {
            "session_id": session["id"],
            "user_hint": "帮我整理一个西安探店图文思路",
            "style_prompt": "",
        }
    ]


def test_stream_confirm_image_count_action_updates_request_state(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])
    state["stage"] = "count_confirmation_required"
    state["style_prompt"] = "生成两张：西安美食图文。"
    service.inspiration_repo.upsert_state(state)

    observed_payloads: list[dict[str, Any]] = []

    monkeypatch.setattr(
        service.creative_agent,
        "respond_stream",
        lambda payload: (
            observed_payloads.append(payload)
            or iter(
                [
                    {
                        "event": "result",
                        "data": {
                            "reply": "张数已确认。",
                            "stage": "count_confirmation_required",
                            "locked": False,
                            "progress": 45,
                            "progress_label": "确认张数",
                            "trace": [],
                        },
                    }
                ]
            )
        ),
    )

    response = client.post(
        "/api/v1/inspirations/messages/stream",
        data={
            "session_id": session["id"],
            "action": "confirm_image_count_2",
            "selected_items": "做 2 张，一张总览一张补充细节，会更舒服",
        },
    )

    assert response.status_code == 200
    assert observed_payloads[0]["state"]["image_count"] == 2


def test_stream_select_image_count_action_updates_request_state(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])
    state["stage"] = "count_confirmation_required"
    state["style_prompt"] = "生成两张：西安美食图文。"
    service.inspiration_repo.upsert_state(state)

    observed_payloads: list[dict[str, Any]] = []

    monkeypatch.setattr(
        service.creative_agent,
        "respond_stream",
        lambda payload: (
            observed_payloads.append(payload)
            or iter(
                [
                    {
                        "event": "result",
                        "data": {
                            "reply": "张数已确认。",
                            "stage": "count_confirmation_required",
                            "locked": False,
                            "progress": 45,
                            "progress_label": "确认张数",
                            "trace": [],
                        },
                    }
                ]
            )
        ),
    )

    response = client.post(
        "/api/v1/inspirations/messages/stream",
        data={
            "session_id": session["id"],
            "action": "select_image_count_2",
            "selected_items": "做 2 张，我想要两个不同画面",
        },
    )

    assert response.status_code == 200
    assert observed_payloads[0]["state"]["image_count"] == 2


def test_stream_set_image_count_action_updates_request_state(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])
    state["stage"] = "count_confirmation_required"
    state["style_prompt"] = "生成两张：西安美食图文。"
    service.inspiration_repo.upsert_state(state)

    observed_payloads: list[dict[str, Any]] = []

    monkeypatch.setattr(
        service.creative_agent,
        "respond_stream",
        lambda payload: (
            observed_payloads.append(payload)
            or iter(
                [
                    {
                        "event": "result",
                        "data": {
                            "reply": "张数已确认。",
                            "stage": "count_confirmation_required",
                            "locked": False,
                            "progress": 45,
                            "progress_label": "确认张数",
                            "trace": [],
                        },
                    }
                ]
            )
        ),
    )

    response = client.post(
        "/api/v1/inspirations/messages/stream",
        data={
            "session_id": session["id"],
            "action": "set_image_count_2",
            "selected_items": "做 2 张，我想要城市漫游加美食清单",
        },
    )

    assert response.status_code == 200
    assert observed_payloads[0]["state"]["image_count"] == 2


def test_stream_textual_image_count_updates_request_state(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])
    state["stage"] = "count_confirmation_required"
    state["style_prompt"] = "生成三张：大理城市漫游与美食图文。"
    service.inspiration_repo.upsert_state(state)

    observed_payloads: list[dict[str, Any]] = []

    monkeypatch.setattr(
        service.creative_agent,
        "respond_stream",
        lambda payload: (
            observed_payloads.append(payload)
            or iter(
                [
                    {
                        "event": "result",
                        "data": {
                            "reply": "收到，我按 3 张来推进。",
                            "stage": "image_count_confirmed",
                            "locked": False,
                            "progress": 58,
                            "progress_label": "已确认张数，准备分图",
                            "trace": [],
                        },
                    }
                ]
            )
        ),
    )

    response = client.post(
        "/api/v1/inspirations/messages/stream",
        data={
            "session_id": session["id"],
            "text": "我想做3张图，一张古城漫游，一张海边散步，一张本地美食。",
        },
    )

    assert response.status_code == 200
    assert observed_payloads[0]["state"]["image_count"] == 3


def test_stream_select_image_count_response_persists_image_count_from_request_state(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])
    state["stage"] = "count_confirmation_required"
    state["style_prompt"] = "生成两张：西安美食图文。"
    service.inspiration_repo.upsert_state(state)

    monkeypatch.setattr(
        service.creative_agent,
        "respond_stream",
        lambda payload: iter(
            [
                {
                    "event": "result",
                    "data": {
                        "reply": "收到，我会按 2 张来推进。",
                        "stage": "image_count_confirmed",
                        "locked": False,
                        "progress": 58,
                        "progress_label": "已确认张数，准备分图",
                        "trace": [],
                    },
                }
            ]
        ),
    )

    response = client.post(
        "/api/v1/inspirations/messages/stream",
        data={
            "session_id": session["id"],
            "action": "select_image_count_2",
            "selected_items": "做 2 张，我想要两个不同画面",
        },
    )

    assert response.status_code == 200
    body = response.text
    events = _parse_sse_body(body)
    assert events[-1]["event"] == "done"
    done_payload = events[-1]["data"]
    assert done_payload["draft"]["image_count"] == 2


def test_creative_agent_respects_dynamic_use_style_profile_response_from_model(tmp_path):
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent(
        llm_provider=_FakeLLMProvider(
            [
                {
                    "decision": "respond_directly",
                    "reason": "先确认用户是沿用大理内容还是切换城市。",
                    "result": {
                        "reply": "我看到你当前会话里已经有大理、洱海和乳扇这组内容了。这次你想沿用它继续，还是换个新的城市主题？",
                        "stage": "style_selected_waiting_content",
                        "locked": False,
                        "progress": 28,
                        "progress_label": "风格已选，待定内容",
                        "draft_style_id": "style-1",
                        "style_payload": {
                            "painting_style": "手绘水彩插画",
                            "color_mood": "暖黄复古",
                            "prompt_example": "请保持旅行手账质感。",
                            "style_prompt": "请保持旅行手账质感。",
                            "extra_keywords": ["纸胶带", "城市漫游"],
                        },
                        "options": {
                            "items": [
                                {"label": "沿用大理这组内容继续", "action_hint": "continue_existing_content"},
                                {"label": "换个城市主题，我来指定", "action_hint": "change_city_or_theme"},
                            ]
                        },
                    },
                }
            ]
        ),
        tools={},
        db_path=tmp_path / "style-profile-city.db",
    )

    result = agent.respond(
        {
            "session_id": "session-1",
            "text": "",
            "selected_items": ["style-1"],
            "attachments": [],
            "action": "use_style_profile",
            "selected_style_profile": {
                "id": "style-1",
                "name": "复古水彩旅行手账",
                "style_payload": {
                    "painting_style": "手绘水彩插画",
                    "color_mood": "暖黄复古",
                    "prompt_example": "请保持旅行手账质感。",
                    "style_prompt": "请保持旅行手账质感。",
                    "extra_keywords": ["纸胶带", "城市漫游"],
                },
            },
            "state": {
                "stage": "initial_understanding",
                "locked": False,
                "style_payload": {},
                "style_prompt": "",
                "asset_candidates": {
                    "locations": ["大理"],
                    "scenes": ["洱海"],
                    "foods": ["乳扇"],
                    "keywords": ["旅行手账"],
                },
                "allocation_plan": [],
                "image_count": None,
            },
        }
    )

    assert result["stage"] == "style_selected_waiting_content"
    assert result["draft_style_id"] == "style-1"
    assert result["style_payload"]["painting_style"] == "手绘水彩插画"
    assert result["reply"].startswith("我看到你当前会话里已经有大理")
    assert result["options"]["items"][0]["label"] == "沿用大理这组内容继续"


def test_stream_overlap_does_not_leak_session_context(client, monkeypatch):
    import threading

    session_a = create_session(client, title="会话A")
    session_b = create_session(client, title="会话B")
    service = client.app.state.services.inspiration
    barrier = threading.Barrier(2)
    observed: list[str] = []

    monkeypatch.setattr(
        service.style_service,
        "chat",
        lambda *, session_id, stage, user_reply, selected_items: (
            barrier.wait(timeout=2),
            observed.append(session_id),
            {
                "reply": f"风格建议-{session_id}",
                "options": {"title": "请选择", "items": ["自然写实"], "max": 1},
                "stage": stage,
                "next_stage": stage,
                "is_finished": False,
                "fallback_used": False,
            },
        )[-1],
    )

    def make_stream(payload):
        service.suggest_painting_style(
            session_id=payload["session_id"],
            stage="painting_style",
            user_reply=payload.get("text") or "",
            selected_items=payload.get("selected_items") or [],
        )
        yield {"event": "result", "data": {"reply": "完成", "stage": "style_ready", "locked": False}}

    monkeypatch.setattr(service.creative_agent, "respond_stream", make_stream)

    responses: list[str] = []

    def run_stream(session_id: str) -> None:
        generator = asyncio.run(
            service.send_message_stream(
                session_id=session_id,
                text="继续",
                selected_items=[],
                action=None,
                image_usages=[],
                images=[],
                videos=[],
            )
        )
        responses.append("".join(list(generator)))

    thread_a = threading.Thread(target=run_stream, args=(session_a["id"],))
    thread_b = threading.Thread(target=run_stream, args=(session_b["id"],))
    thread_a.start()
    thread_b.start()
    thread_a.join()
    thread_b.join()

    assert set(observed) == {session_a["id"], session_b["id"]}
    assert len(responses) == 2


def test_non_stream_endpoint_unchanged(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration

    monkeypatch.setattr(
        service,
        "_run_agent_turn",
        lambda **_: {
            "reply": "普通端点仍然返回完整 JSON。",
            "stage": "style_ready",
            "locked": False,
            "progress": 20,
            "progress_label": "风格确定",
            "options": {"items": [{"label": "继续", "action_hint": "continue"}]},
            "trace": [],
        },
    )

    response = client.post("/api/v1/inspirations/messages", data={"session_id": session["id"], "text": "继续"})

    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["stage"] == "style_ready"
    assert body["messages"][-1]["content"] == "普通端点仍然返回完整 JSON。"
