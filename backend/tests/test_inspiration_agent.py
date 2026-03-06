from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest

from conftest import create_session, create_style, setup_model_routing


@dataclass
class _FakeChatResponse:
    content: str


class _FakeChatModel:
    def __init__(self, decisions: list[dict[str, Any]]):
        self._decisions = list(decisions)
        self.calls: list[list[Any]] = []

    def invoke(self, messages: list[Any]) -> _FakeChatResponse:
        self.calls.append(messages)
        index = len(self.calls) - 1
        return _FakeChatResponse(content=json.dumps(self._decisions[index], ensure_ascii=False))


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

    assert len(llm_provider.model.calls) == 3
    second_turn_summary = llm_provider.model.calls[1][-1].content
    third_turn_summary = llm_provider.model.calls[2][-1].content
    assert "tool_history=" in second_turn_summary
    assert "extract_assets" in second_turn_summary
    assert "generate_images" in third_turn_summary
    assert result["reply"].startswith("我已经把素材整理好")
    assert result["options"]["items"][0]["action_hint"] == "revise"
    assert result["progress"] == 100


def test_creative_agent_system_prompt_loaded_from_external_file():
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent.__new__(CreativeAgent)
    prompt = agent._build_system_prompt()

    assert "友好热情的创作助手" in prompt
    assert "reset_progress" in prompt
    assert "action_hint" in prompt
    assert "progress" in prompt
    assert "emoji" in prompt or "🙂" in prompt or "🎨" in prompt


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
