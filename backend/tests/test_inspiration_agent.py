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


def test_create_app_ignores_legacy_cli_flag_and_always_initializes_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("SAVORY_CANVAS_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SAVORY_CANVAS_STORAGE_DIR", str(tmp_path / "storage"))

    import backend.app.main as main_module

    monkeypatch.setattr(main_module.sys, "argv", ["python", "-m", "backend.app.main", "--legacy"])
    app = main_module.create_app()

    assert not hasattr(main_module, "_is_legacy_inspiration_mode")
    assert not hasattr(app.state.services.inspiration, "agent_mode")
    assert app.state.services.inspiration.creative_agent is not None


def test_create_app_does_not_run_port_cleanup_on_boot(tmp_path, monkeypatch):
    monkeypatch.setenv("SAVORY_CANVAS_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SAVORY_CANVAS_STORAGE_DIR", str(tmp_path / "storage"))

    import backend.app.main as main_module

    app = main_module.create_app()

    assert not hasattr(main_module, "_maybe_cleanup_old_backend_processes")
    assert app.state.services.inspiration.creative_agent is not None


def test_get_inspiration_conversation_includes_agent_meta_by_default(client):
    session = create_session(client)

    response = client.get(f"/api/v1/inspirations/{session['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["agent"]["mode"] == "langgraph"
    assert body["agent"]["trace"] == []


def test_inspiration_message_route_accepts_freeform_action_hint_and_dynamic_options(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration

    monkeypatch.setattr(
        service,
        "_run_agent_turn",
        lambda **_: {
            "reply": "我已经把分图整理好了，我们可以直接锁定，也可以回退重想一下 🙂",
            "stage": "allocation_ready",
            "locked": False,
            "progress": 72,
            "progress_label": "分图预览",
            "options": {
                "items": [
                    {"label": "很棒，就这样锁定吧", "action_hint": "confirm_and_lock"},
                    {"label": "我想再调整一下第二张图", "action_hint": "revise"},
                ]
            },
            "trace": [],
        },
        raising=False,
    )

    response = client.post(
        "/api/v1/inspirations/messages",
        data={"session_id": session["id"], "action": "confirm_and_lock", "text": "先按这个方向继续"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["stage"] == "allocation_ready"
    assert body["draft"]["progress"] == 72
    assert body["draft"]["progress_label"] == "分图预览"
    assert body["draft"]["options"]["items"][0]["label"] == "很棒，就这样锁定吧"
    assert body["draft"]["options"]["items"][0]["action_hint"] == "confirm_and_lock"


def test_creative_agent_tool_node_raises_value_error_for_unknown_tool():
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent.__new__(CreativeAgent)
    agent.tools = {}

    try:
        agent._tool_node(
            {
                "decision": {"tool_name": "missing_tool", "tool_args": {}},
                "request": {"state": {"stage": "initial_understanding", "locked": False}},
                "trace": [],
            }
        )
    except ValueError as exc:
        assert "未知工具" in str(exc)
    else:
        raise AssertionError("unknown tool should raise ValueError")


def test_creative_agent_tool_node_force_overrides_suggest_style_session_id():
    from backend.app.agent.creative_agent import CreativeAgent
    import threading

    observed: dict[str, Any] = {}
    agent = CreativeAgent.__new__(CreativeAgent)
    agent._stream_local = threading.local()
    agent.tools = {
        "suggest_painting_style": _ToolStub(lambda args: observed.update(args) or {"reply": "ok"}),
    }

    result = agent._tool_node(
        {
            "decision": {
                "tool_name": "suggest_painting_style",
                "tool_args": {
                    "session_id": "wrong-session",
                    "stage": "painting_style",
                    "user_reply": "我想做西安攻略",
                    "selected_items": [],
                },
            },
            "request": {
                "session_id": "correct-session",
                "state": {"stage": "initial_understanding", "locked": False},
            },
            "trace": [],
        }
    )

    assert observed["session_id"] == "correct-session"
    assert result["tool_call_count"] == 1


def test_creative_agent_tool_node_fills_missing_suggest_style_args_from_request():
    from backend.app.agent.creative_agent import CreativeAgent
    import threading

    observed: dict[str, Any] = {}
    agent = CreativeAgent.__new__(CreativeAgent)
    agent._stream_local = threading.local()
    agent.tools = {
        "suggest_painting_style": _ToolStub(lambda args: observed.update(args) or {"reply": "ok"}),
    }

    agent._tool_node(
        {
            "decision": {
                "tool_name": "suggest_painting_style",
                "tool_args": {
                    "session_id": "wrong-session",
                },
            },
            "request": {
                "session_id": "correct-session",
                "text": "我想做西安攻略",
                "selected_items": ["自然写实"],
                "state": {"stage": "initial_understanding", "style_stage": "painting_style", "locked": False},
            },
            "trace": [],
        }
    )

    assert observed["session_id"] == "correct-session"
    assert observed["user_reply"] == "我想做西安攻略"
    assert observed["selected_items"] == ["自然写实"]
    assert observed["stage"] == "painting_style"


def test_creative_agent_tool_node_uses_current_style_stage_when_stage_missing():
    from backend.app.agent.creative_agent import CreativeAgent
    import threading

    observed: dict[str, Any] = {}
    agent = CreativeAgent.__new__(CreativeAgent)
    agent._stream_local = threading.local()
    agent.tools = {
        "suggest_painting_style": _ToolStub(lambda args: observed.update(args) or {"reply": "ok"}),
    }

    agent._tool_node(
        {
            "decision": {
                "tool_name": "suggest_painting_style",
                "tool_args": {
                    "session_id": "wrong-session",
                },
            },
            "request": {
                "session_id": "correct-session",
                "text": "继续",
                "selected_items": [],
                "state": {"stage": "style_ready", "style_stage": "color_mood", "locked": False},
            },
            "trace": [],
        }
    )

    assert observed["stage"] == "color_mood"


def test_creative_agent_tool_node_fills_missing_extract_assets_args_from_request():
    from backend.app.agent.creative_agent import CreativeAgent
    import threading

    observed: dict[str, Any] = {}
    agent = CreativeAgent.__new__(CreativeAgent)
    agent._stream_local = threading.local()
    agent.tools = {
        "extract_assets": _ToolStub(lambda args: observed.update(args) or {"locations": ["西安"], "keywords": ["探店"]}),
    }

    agent._tool_node(
        {
            "decision": {
                "tool_name": "extract_assets",
                "tool_args": {
                    "session_id": "wrong-session",
                },
            },
            "request": {
                "session_id": "correct-session",
                "text": "帮我整理一个西安探店图文思路",
                "selected_items": [],
                "state": {"stage": "initial_understanding", "style_prompt": "", "locked": False},
            },
            "trace": [],
        }
    )

    assert observed["session_id"] == "correct-session"
    assert observed["user_hint"] == "帮我整理一个西安探店图文思路"
    assert observed["style_prompt"] == ""


def test_creative_agent_capture_tool_output_returns_structured_data_only():
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent.__new__(CreativeAgent)

    asset_capture = agent._capture_tool_output(
        tool_name="extract_assets",
        tool_result={"foods": ["羊肉泡馍"], "scenes": ["城墙"]},
        request={"state": {"stage": "prompt_ready"}},
    )
    image_capture = agent._capture_tool_output(
        tool_name="generate_images",
        tool_result={"job_id": "job-123", "status": "queued"},
        request={"state": {"stage": "allocation_ready"}},
    )

    assert asset_capture == {"asset_candidates": {"foods": ["羊肉泡馍"], "scenes": ["城墙"]}}
    assert image_capture == {"active_job_id": "job-123", "job_status": "queued"}
    assert "reply" not in image_capture
    assert "options" not in image_capture


def test_creative_agent_capture_tool_output_updates_style_stage_from_suggest_result():
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent.__new__(CreativeAgent)

    capture = agent._capture_tool_output(
        tool_name="suggest_painting_style",
        tool_result={
            "reply": "接下来看看背景装饰。",
            "stage": "background_decor",
            "next_stage": "color_mood",
            "options": {"title": "请选择", "items": ["手账贴纸"], "max": 1},
            "is_finished": False,
            "fallback_used": False,
        },
        request={"state": {"style_stage": "painting_style"}},
    )

    assert capture["style_stage"] == "background_decor"


def test_creative_agent_multi_tool_roundtrip_accumulates_tool_history(tmp_path):
    from backend.app.agent.creative_agent import CreativeAgent

    decisions = [
        {
            "decision": "use_tool",
            "reason": "先提取素材。",
            "tool_name": "extract_assets",
            "tool_args": {"session_id": "session-1", "user_hint": "西安攻略", "style_prompt": ""},
            "dynamic_stage": "asset_extracting",
            "dynamic_stage_label": "提取素材",
        },
        {
            "decision": "use_tool",
            "reason": "素材齐了，直接启动生成。",
            "tool_name": "generate_images",
            "tool_args": {"session_id": "session-1"},
            "dynamic_stage": "auto_generating",
            "dynamic_stage_label": "自动生成",
        },
        {
            "decision": "respond_directly",
            "reason": "已经自动启动生成，向用户汇报结果。",
            "result": {
                "reply": "我已经把素材整理好，并直接帮你启动生成啦 🎨",
                "stage": "generation_started",
                "locked": True,
                "progress": 100,
                "progress_label": "生成已启动",
                "options": {
                    "items": [
                        {"label": "我想再调整一下构图", "action_hint": "revise"},
                        {"label": "先保持这个方向", "action_hint": "continue"},
                    ]
                },
            },
        },
    ]
    llm_provider = _FakeLLMProvider(decisions)
    agent = CreativeAgent(
        llm_provider=llm_provider,
        tools={
            "extract_assets": _ToolStub(lambda _: {"foods": ["羊肉泡馍"], "scenes": ["城墙"]}),
            "generate_images": _ToolStub(lambda _: {"job_id": "job-123", "status": "queued"}),
        },
        db_path=tmp_path / "agent.db",
    )

    result = agent.respond(
        {
            "session_id": "session-1",
            "text": "我要做西安三张图攻略",
            "selected_items": [],
            "attachments": [],
            "action": None,
            "state": {
                "stage": "initial_understanding",
                "locked": False,
                "style_payload": {},
                "style_prompt": "",
                "asset_candidates": {},
                "allocation_plan": [],
                "image_count": 3,
                "draft_style_id": "draft-style-1",
            },
        }
    )

    assert len(llm_provider.model.calls) == 2
    second_turn_summary = llm_provider.model.calls[1][-1].content
    assert "tool_history=" in second_turn_summary
    assert "extract_assets" in second_turn_summary
    assert result["active_job_id"] == "job-123"
    assert result["reply"].startswith("生成任务已经启动了")
    assert result.get("options") in (None, {"items": []})
    assert result["progress"] == 85


def test_generate_style_prompt_without_image_count_marks_count_confirmation_required():
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent.__new__(CreativeAgent)
    capture = agent._capture_tool_output(
        tool_name="generate_style_prompt",
        tool_result={"style_prompt": "生成一张：西安探店图文思路。", "image_count": None},
        request={"state": {"progress": 30}},
    )

    assert capture["stage"] == "count_confirmation_required"
    assert capture["progress_label"] == "确认张数"
    assert capture["prompt_confirmable"] is False


def test_creative_agent_blocks_allocate_without_image_count(tmp_path):
    from backend.app.agent.creative_agent import CreativeAgent

    decisions = [
        {
            "decision": "use_tool",
            "reason": "先生成提示词。",
            "tool_name": "generate_style_prompt",
            "tool_args": {"session_id": "session-1", "feedback": "继续"},
        },
        {
            "decision": "use_tool",
            "reason": "接着直接分图。",
            "tool_name": "allocate_assets_to_images",
            "tool_args": {"session_id": "session-1", "user_hint": "继续"},
        },
    ]
    allocate_called = {"value": False}
    agent = CreativeAgent(
        llm_provider=_FakeLLMProvider(decisions),
        tools={
            "generate_style_prompt": _ToolStub(lambda _: {"style_prompt": "生成一张：西安探店图文思路。", "image_count": None}),
            "allocate_assets_to_images": _ToolStub(lambda _: allocate_called.update(value=True) or []),
        },
        db_path=tmp_path / "agent.db",
    )

    result = agent.respond(
        {
            "session_id": "session-1",
            "text": "继续整理",
            "selected_items": [],
            "attachments": [],
            "action": None,
            "state": {
                "stage": "initial_understanding",
                "locked": False,
                "style_payload": {},
                "style_prompt": "",
                "asset_candidates": {},
                "allocation_plan": [],
            },
        }
    )

    assert allocate_called["value"] is False
    assert result["stage"] == "count_confirmation_required"
    assert result["progress_label"] == "确认张数"
    assert "先确认这次要生成几张图" in result["reply"]




def test_creative_agent_redirects_redundant_extract_assets_after_count_confirmation(tmp_path):
    from backend.app.agent.creative_agent import CreativeAgent

    decisions = [
        {
            "decision": "use_tool",
            "reason": "先提取素材。",
            "tool_name": "extract_assets",
            "tool_args": {"session_id": "session-1"},
        },
        {
            "decision": "respond_directly",
            "reason": "提示词已整理。",
            "result": {
                "reply": "我已经整理好了提示词。",
                "stage": "prompt_ready",
                "locked": False,
            },
        },
    ]
    observed = {"extract": 0, "prompt": 0}
    agent = CreativeAgent(
        llm_provider=_FakeLLMProvider(decisions),
        tools={
            "extract_assets": _ToolStub(lambda _: observed.__setitem__("extract", observed["extract"] + 1) or {}),
            "generate_style_prompt": _ToolStub(lambda _: observed.__setitem__("prompt", observed["prompt"] + 1) or {"style_prompt": "生成一张大理洱海图解海报。", "image_count": 2}),
        },
        db_path=tmp_path / "agent-redirect-extract.db",
    )

    events = list(
        agent.respond_stream(
            {
                "session_id": "session-1",
                "text": "",
                "selected_items": ["生成2张图，内容更丰富"],
                "action": "set_image_count_2",
                "attachments": [],
                "state": {
                    "stage": "count_confirmation_required",
                    "locked": False,
                    "image_count": 2,
                    "asset_candidates": {"locations": ["大理"], "scenes": ["洱海", "漫步"], "foods": [], "keywords": ["大理洱海漫步"]},
                    "style_prompt": "",
                    "allocation_plan": [],
                },
            }
        )
    )

    assert observed["extract"] == 0
    assert observed["prompt"] == 1
    assert any(event["event"] == "tool_start" and event["data"]["tool_name"] == "generate_style_prompt" for event in events)


def test_creative_agent_allows_allocate_when_image_count_confirmed(tmp_path):
    from backend.app.agent.creative_agent import CreativeAgent

    decisions = [
        {
            "decision": "use_tool",
            "reason": "现在可以分图了。",
            "tool_name": "allocate_assets_to_images",
            "tool_args": {"session_id": "session-1", "user_hint": "继续"},
        },
        {
            "decision": "respond_directly",
            "reason": "分图已经完成。",
            "result": {
                "reply": "分图方案已经整理好了。",
                "stage": "allocation_ready",
                "locked": False,
                "progress": 72,
                "progress_label": "分图预览",
            },
        },
    ]
    allocate_called = {"value": False}
    agent = CreativeAgent(
        llm_provider=_FakeLLMProvider(decisions),
        tools={
            "allocate_assets_to_images": _ToolStub(lambda _: allocate_called.update(value=True) or [{"slot_index": 1, "focus_title": "城墙主图"}]),
        },
        db_path=tmp_path / "agent.db",
    )

    result = agent.respond(
        {
            "session_id": "session-1",
            "text": "继续整理",
            "selected_items": [],
            "attachments": [],
            "action": None,
            "state": {
                "stage": "prompt_ready",
                "locked": False,
                "style_payload": {},
                "style_prompt": "生成一张：西安探店图文思路。",
                "asset_candidates": {"locations": ["西安"]},
                "allocation_plan": [],
                "image_count": 3,
            },
        }
    )

    assert allocate_called["value"] is True
    assert result["stage"] == "allocation_ready"


def test_creative_agent_stream_does_not_start_allocate_without_image_count(tmp_path):
    from backend.app.agent.creative_agent import CreativeAgent

    decisions = [
        {
            "decision": "use_tool",
            "reason": "先生成提示词。",
            "tool_name": "generate_style_prompt",
            "tool_args": {"session_id": "session-1", "feedback": "继续"},
        },
        {
            "decision": "use_tool",
            "reason": "接着分图。",
            "tool_name": "allocate_assets_to_images",
            "tool_args": {"session_id": "session-1", "user_hint": "继续"},
        },
    ]
    allocate_called = {"value": False}
    agent = CreativeAgent(
        llm_provider=_FakeLLMProvider(decisions),
        tools={
            "generate_style_prompt": _ToolStub(lambda _: {"style_prompt": "生成一张：西安探店图文思路。", "image_count": None}),
            "allocate_assets_to_images": _ToolStub(lambda _: allocate_called.update(value=True) or []),
        },
        db_path=tmp_path / "agent.db",
    )

    events = list(
        agent.respond_stream(
            {
                "session_id": "session-1",
                "text": "继续整理",
                "selected_items": [],
                "attachments": [],
                "action": None,
                "state": {
                    "stage": "initial_understanding",
                    "locked": False,
                    "style_payload": {},
                    "style_prompt": "",
                    "asset_candidates": {},
                    "allocation_plan": [],
                },
            }
        )
    )

    event_names = [event["event"] for event in events]
    assert event_names == ["thinking", "thinking", "tool_start", "tool_done", "thinking", "thinking", "result"]
    assert allocate_called["value"] is False


def test_creative_agent_respond_stream_emits_thinking_before_tool(tmp_path):
    from backend.app.agent.creative_agent import CreativeAgent

    decisions = [
        {
            "decision": "use_tool",
            "reason": "先提取素材。",
            "tool_name": "extract_assets",
            "tool_args": {"session_id": "session-1", "user_hint": "西安攻略", "style_prompt": ""},
        },
        {
            "decision": "respond_directly",
            "reason": "素材提取完成，向用户汇报。",
            "result": {
                "reply": "我先把素材整理出来了。",
                "stage": "asset_ready",
                "locked": False,
                "progress": 50,
                "progress_label": "素材已整理",
            },
        },
    ]
    agent = CreativeAgent(
        llm_provider=_FakeLLMProvider(decisions),
        tools={"extract_assets": _ToolStub(lambda _: {"foods": ["羊肉泡馍"], "scenes": ["城墙"], "keywords": ["西安攻略"]})},
        db_path=tmp_path / "agent.db",
    )

    events = list(
        agent.respond_stream(
            {
                "session_id": "session-1",
                "text": "我要做西安攻略",
                "selected_items": [],
                "attachments": [],
                "state": {"stage": "initial_understanding", "locked": False},
            }
        )
    )

    assert [event["event"] for event in events] == ["thinking", "thinking", "tool_start", "tool_done", "thinking", "thinking", "result"]
    assert events[1]["data"]["message"] == "先提取素材。"
    assert events[2]["data"]["tool_name"] == "extract_assets"
    assert events[3]["data"]["duration_ms"] >= 0
    assert events[5]["data"]["message"] == "素材提取完成，向用户汇报。"


def test_creative_agent_respond_stream_emits_reasoning_summary_text(tmp_path):
    from backend.app.agent.creative_agent import CreativeAgent

    decisions = [
        {
            "__reasoning_summaries": ["**先确认素材范围，再调用提取工具。**"],
            "decision": "use_tool",
            "reason": "先提取素材。",
            "tool_name": "extract_assets",
            "tool_args": {"session_id": "session-1", "user_hint": "西安攻略", "style_prompt": ""},
        },
        {
            "decision": "respond_directly",
            "reason": "素材提取完成，向用户汇报。",
            "result": {
                "reply": "我先把素材整理出来了。",
                "stage": "asset_ready",
                "locked": False,
            },
        },
    ]
    agent = CreativeAgent(
        llm_provider=_FakeLLMProvider(decisions),
        tools={"extract_assets": _ToolStub(lambda _: {"foods": ["羊肉泡馍"]})},
        db_path=tmp_path / "agent-reasoning.db",
    )

    events = list(
        agent.respond_stream(
            {
                "session_id": "session-1",
                "text": "我要做西安攻略",
                "selected_items": [],
                "attachments": [],
                "state": {"stage": "initial_understanding", "locked": False},
            }
        )
    )

    assert [event["event"] for event in events] == ["thinking", "thinking", "tool_start", "tool_done", "thinking", "thinking", "result"]
    assert events[1]["data"]["message"] == "**先确认素材范围，再调用提取工具。**"


def test_creative_agent_system_prompt_loaded_from_external_file():
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent.__new__(CreativeAgent)
    prompt = agent._build_system_prompt()

    assert "创作主脑 Agent" in prompt
    assert "style_profile_content_confirmation_needed" in prompt
    assert "不要机械复用固定句式" in prompt
    assert "不要连续多轮返回几乎一样的 reply 和 options" in prompt
    assert "如果 `style_profile_content_confirmation_needed=True`" in prompt
    assert "Few-shot 示例" in prompt
    assert "generate_images" in prompt


def test_agent_llm_provider_uses_responses_protocol(monkeypatch):
    from backend.app.agent.llm_provider import AgentLLMProvider, _AgentProtocolCaller

    captured: dict[str, Any] = {}

    class FakeProviderRepo:
        def get(self, provider_id: str) -> dict[str, Any] | None:
            if provider_id != "provider-1":
                return None
            return {
                "id": "provider-1",
                "enabled": True,
                "api_key": "secret",
                "base_url": "https://example.com/v1",
                "api_protocol": "responses",
            }

    class FakeModelService:
        provider_repo = FakeProviderRepo()

        def require_routing(self) -> dict[str, Any]:
            return {"text_model": {"provider_id": "provider-1", "model_name": "gpt-5.4"}}

    def fake_post_json(self, url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        captured["url"] = url
        captured["payload"] = payload
        return {
            "output_text": "ok",
            "output": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "**Providing friendly greeting**"}],
                }
            ],
        }

    monkeypatch.setattr(_AgentProtocolCaller, "_post_json", fake_post_json)

    model = AgentLLMProvider(model_service=FakeModelService()).build_chat_model()
    response = model.invoke([SystemMessage(content="你是助手"), HumanMessage(content="你好")])

    assert captured["url"].endswith("/responses")
    assert captured["payload"]["reasoning"]["summary"] == "auto"
    assert response.content == "ok"
    assert response.reasoning_summaries == ["**Providing friendly greeting**"]


def test_agent_llm_provider_uses_chat_completions_protocol(monkeypatch):
    from backend.app.agent.llm_provider import AgentLLMProvider, _AgentProtocolCaller

    captured: dict[str, Any] = {}

    class FakeProviderRepo:
        def get(self, provider_id: str) -> dict[str, Any] | None:
            if provider_id != "provider-1":
                return None
            return {
                "id": "provider-1",
                "enabled": True,
                "api_key": "secret",
                "base_url": "https://example.com/v1",
                "api_protocol": "chat_completions",
            }

    class FakeModelService:
        provider_repo = FakeProviderRepo()

        def require_routing(self) -> dict[str, Any]:
            return {"text_model": {"provider_id": "provider-1", "model_name": "gpt-5.4"}}

    def fake_post_json(self, url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        captured["url"] = url
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(_AgentProtocolCaller, "_post_json", fake_post_json)

    model = AgentLLMProvider(model_service=FakeModelService()).build_chat_model()
    response = model.invoke([SystemMessage(content="你是助手"), HumanMessage(content="你好")])

    assert captured["url"].endswith("/chat/completions")
    assert response.content == "ok"


def test_agent_llm_provider_retries_retryable_upstream_error(monkeypatch):
    from backend.app.agent.llm_provider import AgentLLMProvider, _AgentProtocolCaller
    from backend.app.services.style.errors import StyleFallbackError

    attempts = {"count": 0}

    class FakeProviderRepo:
        def get(self, provider_id: str) -> dict[str, Any] | None:
            if provider_id != "provider-1":
                return None
            return {
                "id": "provider-1",
                "enabled": True,
                "api_key": "secret",
                "base_url": "https://example.com/v1",
                "api_protocol": "responses",
            }

    class FakeModelService:
        provider_repo = FakeProviderRepo()

        def require_routing(self) -> dict[str, Any]:
            return {"text_model": {"provider_id": "provider-1", "model_name": "gpt-5.4"}}

    def flaky_post_json(self, url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise StyleFallbackError("upstream_timeout_or_network", "temporary timeout")
        return {"output_text": "ok"}

    monkeypatch.setattr(_AgentProtocolCaller, "_post_json", flaky_post_json)

    model = AgentLLMProvider(model_service=FakeModelService()).build_chat_model()
    response = model.invoke([SystemMessage(content="你是助手"), HumanMessage(content="你好")])

    assert attempts["count"] == 2
    assert response.content == "ok"


def test_apply_agent_turn_supports_dynamic_options_progress_and_active_job(client):
    session = create_session(client)
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])

    service._apply_agent_turn(
        session_id=session["id"],
        state=state,
        turn={
            "reply": "我已经直接帮你启动生成，现在右侧会开始刷新进度 🎨",
            "stage": "generation_started",
            "locked": True,
            "progress": 100,
            "progress_label": "生成已启动",
            "active_job_id": "job-123",
            "options": {
                "items": [
                    {"label": "我想回到分图再调一下", "action_hint": "revise"},
                    {"label": "先这样继续生成", "action_hint": "continue"},
                ]
            },
            "trace": [],
        },
    )

    conversation = service.get_conversation(session["id"])

    assert conversation["draft"]["stage"] == "generation_started"
    assert conversation["draft"]["progress"] == 100
    assert conversation["draft"]["progress_label"] == "生成已启动"
    assert conversation["draft"]["active_job_id"] == "job-123"
    assert conversation["draft"]["options"]["items"][0]["label"] == "我想回到分图再调一下"


def test_apply_agent_turn_persists_updated_style_stage(client):
    from backend.app.agent.creative_agent import CreativeAgent

    session = create_session(client)
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])

    agent = CreativeAgent.__new__(CreativeAgent)
    request = {
        "session_id": session["id"],
        "state": {
            "stage": "initial_understanding",
            "style_stage": "painting_style",
            "locked": False,
        },
    }
    captured = agent._capture_tool_output(
        tool_name="suggest_painting_style",
        tool_result={
            "reply": "我们接下来看看背景装饰。",
            "stage": "background_decor",
            "next_stage": "color_mood",
            "options": {"title": "请选择", "items": ["手账贴纸"], "max": 1},
            "is_finished": False,
            "fallback_used": False,
        },
        request=request,
    )
    merged_request = agent._merge_result_into_request(request, "suggest_painting_style", captured)
    turn = agent._capture_direct_response(
        merged_request,
        {
            "reply": "先看看背景装饰方向。",
            "stage": "style_ready",
            "locked": False,
        },
    )

    service._apply_agent_turn(session_id=session["id"], state=state, turn=turn)

    updated = service._ensure_state(session["id"])
    assert updated["style_stage"] == "background_decor"


def test_apply_agent_turn_force_unlocks_non_terminal_stage(client):
    session = create_session(client)
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])

    service._apply_agent_turn(
        session_id=session["id"],
        state=state,
        turn={
            "reply": "先继续确认色彩情绪。",
            "stage": "color_mood",
            "locked": True,
            "progress": 26,
            "progress_label": "确认色彩情绪",
            "options": {"items": [{"label": "继续", "action_hint": "continue"}]},
            "trace": [],
        },
    )

    updated = service._ensure_state(session["id"])
    assert updated["locked"] is False


