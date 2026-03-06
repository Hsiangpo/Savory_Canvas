from __future__ import annotations

import asyncio
from typing import Any

import pytest

from conftest import create_session, create_style, setup_model_routing


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


def test_inspiration_message_returns_langgraph_agent_meta_when_agent_succeeds(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration

    def fake_run_agent_turn(**_: Any) -> dict[str, Any]:
        return {
            "reply": "已进入 Agent 提示词确认阶段。",
            "stage": "prompt_revision",
            "locked": False,
            "style_payload": {
                "painting_style": "手绘插画",
                "color_mood": "温暖治愈",
                "prompt_example": "请保持统一风格与清晰图文布局。",
                "style_prompt": "生成一张：以西安城墙和羊肉泡馍为核心的信息图。",
                "extra_keywords": [],
            },
            "style_prompt": "生成一张：以西安城墙和羊肉泡馍为核心的信息图。",
            "options": {"title": "请选择下一步", "items": ["确认提示词"], "max": 1},
            "dynamic_stage": "style_discovery",
            "dynamic_stage_label": "Agent 风格分析",
            "trace": [
                {
                    "id": "trace-1",
                    "node": "tool_node",
                    "decision": "suggest_style",
                    "tool_name": "suggest_painting_style",
                    "summary": "已根据用户输入完成风格分析。",
                    "status": "completed",
                    "created_at": "2026-03-06T00:00:00Z",
                }
            ],
            "requirement_ready": True,
            "prompt_confirmable": True,
        }

    monkeypatch.setattr(service, "_run_agent_turn", fake_run_agent_turn, raising=False)

    response = client.post(
        "/api/v1/inspirations/messages",
        data={"session_id": session["id"], "text": "我想做一篇西安美食和景点攻略"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["stage"] == "prompt_revision"
    assert body["agent"]["mode"] == "langgraph"
    assert body["agent"]["dynamic_stage"] == "style_discovery"
    assert body["agent"]["dynamic_stage_label"] == "Agent 风格分析"
    assert body["agent"]["trace"][0]["tool_name"] == "suggest_painting_style"


def test_creative_agent_tool_node_raises_value_error_for_unknown_tool():
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent.__new__(CreativeAgent)
    agent.tools = {}

    try:
        agent._tool_node(
            {
                "decision": {"tool_name": "missing_tool", "tool_args": {}},
                "request": {"state": {"stage": "style_collecting", "locked": False}},
                "trace": [],
            }
        )
    except ValueError as exc:
        assert "未知工具" in str(exc)
    else:
        raise AssertionError("unknown tool should raise ValueError")


def test_creative_agent_build_turn_result_maps_extract_assets():
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent.__new__(CreativeAgent)
    result = agent._build_turn_result(
        request={"state": {"stage": "prompt_revision", "locked": False}},
        decision={"dynamic_stage": "asset_extraction", "dynamic_stage_label": "资产提取"},
        tool_name="extract_assets",
        tool_result={
            "locations": ["西安"],
            "foods": ["羊肉泡馍"],
            "scenes": ["城墙"],
            "keywords": ["攻略"],
            "source_asset_ids": ["asset-1"],
            "confidence": 0.9,
        },
        trace=[],
    )

    assert result["stage"] == "asset_confirming"
    assert result["asset_candidates"]["foods"] == ["羊肉泡馍"]
    assert result["dynamic_stage"] == "asset_extraction"


def test_creative_agent_build_turn_result_maps_generate_style_prompt():
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent.__new__(CreativeAgent)
    result = agent._build_turn_result(
        request={"state": {"stage": "style_collecting", "locked": False}},
        decision={"dynamic_stage": "prompt_generation", "dynamic_stage_label": "提示词生成"},
        tool_name="generate_style_prompt",
        tool_result={
            "style_prompt": "生成一张：西安城墙与泡馍攻略图。",
            "image_count": 2,
        },
        trace=[],
    )

    assert result["stage"] == "prompt_revision"
    assert result["style_prompt"] == "生成一张：西安城墙与泡馍攻略图。"
    assert result["image_count"] == 2


def test_creative_agent_build_turn_result_maps_allocate_assets_to_images():
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent.__new__(CreativeAgent)
    plan = [
        {
            "slot_index": 1,
            "focus_title": "城墙主图",
            "focus_description": "突出城墙夜景。",
            "source_asset_ids": ["asset-1"],
            "confirmed": False,
        }
    ]
    result = agent._build_turn_result(
        request={"state": {"stage": "prompt_revision", "locked": False}},
        decision={"dynamic_stage": "allocation", "dynamic_stage_label": "分图策划"},
        tool_name="allocate_assets_to_images",
        tool_result=plan,
        trace=[],
    )

    assert result["stage"] == "asset_confirming"
    assert result["allocation_plan"] == plan
    assert result["options"]["items"] == ["确认分图并锁定", "继续调整分图"]


def test_creative_agent_build_turn_result_maps_save_style_generate_images_and_copy():
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent.__new__(CreativeAgent)
    save_result = agent._build_turn_result(
        request={"state": {"stage": "locked", "locked": True, "draft_style_id": "draft-style-1"}},
        decision={"dynamic_stage": "style_save", "dynamic_stage_label": "风格归档"},
        tool_name="save_style",
        tool_result={"style_id": "style-123", "status": "saved", "style_name": "西安复古手账"},
        trace=[],
    )
    image_result = agent._build_turn_result(
        request={"state": {"stage": "locked", "locked": True, "draft_style_id": "draft-style-1"}},
        decision={"dynamic_stage": "generation", "dynamic_stage_label": "生成执行"},
        tool_name="generate_images",
        tool_result={"job_id": "job-123", "status": "queued"},
        trace=[],
    )
    copy_result = agent._build_turn_result(
        request={"state": {"stage": "locked", "locked": True}},
        decision={"dynamic_stage": "copy_generation", "dynamic_stage_label": "文案生成"},
        tool_name="generate_copy",
        tool_result={"job_id": "job-123", "status": "queued"},
        trace=[],
    )

    assert save_result["stage"] == "locked"
    assert save_result["locked"] is True
    assert save_result["draft_style_id"] == "style-123"
    assert image_result["stage"] == "locked"
    assert image_result["locked"] is True
    assert image_result["job_id"] == "job-123"
    assert image_result["options"] == {"title": "请选择下一步", "items": ["保存风格", "暂不保存"], "max": 1}
    assert copy_result["job_id"] == "job-123"
    assert copy_result["status"] == "queued"


def test_creative_agent_build_input_summary_contains_richer_context():
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent.__new__(CreativeAgent)
    summary = agent._build_input_summary(
        {
            "session_id": "session-1",
            "text": "请继续",
            "selected_items": ["自然写实"],
            "attachments": [{"type": "image", "usage_type": "style_reference"}],
            "state": {
                "stage": "prompt_revision",
                "locked": False,
                "style_payload": {"painting_style": "自然写实"},
                "style_prompt": "生成一张：西安城墙攻略图。",
                "asset_candidates": {"foods": ["羊肉泡馍"], "scenes": ["城墙"]},
                "allocation_plan": [{"slot_index": 1, "focus_title": "城墙主图"}],
                "image_count": 2,
            },
            "content_mode": "food_scenic",
        }
    )

    assert "content_mode=food_scenic" in summary
    assert "style_payload=" in summary
    assert "asset_candidates=" in summary
    assert "allocation_plan=" in summary
    assert "style_prompt=" in summary
    assert 'attachments=[{"type": "image", "usage_type": "style_reference"}]' in summary


def test_creative_agent_system_prompt_loaded_from_external_file():
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent.__new__(CreativeAgent)
    prompt = agent._build_system_prompt()

    assert "extract_assets" in prompt
    assert "generate_style_prompt" in prompt
    assert "allocate_assets_to_images" in prompt
    assert "save_style" in prompt
    assert "generate_images" in prompt
    assert "generate_copy" in prompt
    assert "respond_directly" in prompt


def test_apply_agent_turn_preserves_existing_allocation_and_locked_for_sparse_respond_directly(client):
    session = create_session(client)
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])
    state["stage"] = "asset_confirming"
    state["locked"] = True
    state["allocation_plan"] = [
        {
            "slot_index": 1,
            "focus_title": "城墙主图",
            "focus_description": "保留已有分图。",
            "source_asset_ids": ["asset-1"],
            "confirmed": True,
        }
    ]

    service._apply_agent_turn(
        session_id=session["id"],
        state=state,
        turn={
            "reply": "这些图可以继续做 3:4 和 4:5 版式。",
            "stage": "asset_confirming",
            "trace": [],
        },
    )

    assert state["locked"] is True
    assert len(state["allocation_plan"]) == 1
    assert state["allocation_plan"][0]["focus_title"] == "城墙主图"


def test_build_creative_tools_supports_save_style_and_session_generation():
    from backend.app.agent.tools import build_creative_tools

    calls: list[tuple[str, str]] = []

    class RuntimeStub:
        def save_style_from_agent(self, *, session_id: str) -> dict[str, Any]:
            calls.append(("save_style_from_agent", session_id))
            return {"style_id": "style-1", "status": "saved"}

        def generate_images(self, *, session_id: str) -> dict[str, Any]:
            calls.append(("generate_images", session_id))
            return {"job_id": "job-1", "status": "queued"}

    tools = build_creative_tools(RuntimeStub())
    save_result = tools["save_style"].invoke({"session_id": "session-1"})
    image_result = tools["generate_images"].invoke({"session_id": "session-1"})

    assert save_result["style_id"] == "style-1"
    assert image_result["job_id"] == "job-1"
    assert calls == [("save_style_from_agent", "session-1"), ("generate_images", "session-1")]


def test_inspiration_service_save_style_from_agent_creates_style(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])
    state["stage"] = "locked"
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


def test_inspiration_service_generate_images_creates_job_from_session_state(client, monkeypatch):
    session = create_session(client, content_mode="food_scenic")
    style = create_style(client, session["id"], {"painting_style": "自然写实"})
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])
    state["stage"] = "locked"
    state["locked"] = True
    state["draft_style_id"] = style["id"]
    state["image_count"] = 2
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


def test_agent_only_end_to_end_can_lock_and_generate(client, monkeypatch):
    setup_model_routing(client)
    session = create_session(client, title="agent主脑流程", content_mode="food_scenic")
    style = create_style(client, session["id"], {"painting_style": "自然写实"})
    service = client.app.state.services.inspiration

    def fake_run_agent_turn(**kwargs: Any) -> dict[str, Any]:
        action = kwargs.get("action")
        selected_items = kwargs.get("selected_items") or []
        if action == "confirm_allocation_plan":
            return {
                "reply": "方案已锁定，可开始生成。",
                "stage": "locked",
                "locked": True,
                "draft_style_id": style["id"],
                "allocation_plan": [
                    {
                        "slot_index": 1,
                        "focus_title": "城墙与泡馍",
                        "focus_description": "图解西安城墙与羊肉泡馍。",
                        "source_asset_ids": ["asset-1"],
                        "confirmed": True,
                    }
                ],
                "dynamic_stage": "locked",
                "dynamic_stage_label": "方案锁定",
                "trace": [],
            }
        if action == "confirm_prompt":
            return {
                "reply": "已完成分图规划，请确认锁定。",
                "stage": "asset_confirming",
                "locked": False,
                "draft_style_id": style["id"],
                "allocation_plan": [
                    {
                        "slot_index": 1,
                        "focus_title": "城墙与泡馍",
                        "focus_description": "图解西安城墙与羊肉泡馍。",
                        "source_asset_ids": ["asset-1"],
                        "confirmed": False,
                    }
                ],
                "asset_candidates": {"foods": ["羊肉泡馍"], "scenes": ["城墙"], "keywords": ["西安攻略"], "source_asset_ids": ["asset-1"]},
                "options": {"title": "请选择下一步", "items": ["确认分图并锁定"], "max": 1},
                "dynamic_stage": "allocation",
                "dynamic_stage_label": "分图策划",
                "trace": [],
            }
        if selected_items:
            return {
                "reply": "已生成提示词，请确认。",
                "stage": "prompt_revision",
                "locked": False,
                "draft_style_id": style["id"],
                "style_payload": {
                    "painting_style": "自然写实",
                    "color_mood": "温暖治愈",
                    "prompt_example": "请保持统一风格与清晰图文布局。",
                    "style_prompt": "生成一张：西安城墙与羊肉泡馍攻略图。",
                    "extra_keywords": [],
                },
                "style_prompt": "生成一张：西安城墙与羊肉泡馍攻略图。",
                "image_count": 1,
                "options": {"title": "请选择下一步", "items": ["确认提示词"], "max": 1},
                "dynamic_stage": "prompt_generation",
                "dynamic_stage_label": "提示词生成",
                "trace": [],
            }
        return {
            "reply": "请选择风格方向。",
            "stage": "style_collecting",
            "locked": False,
            "options": {"title": "请选择绘画风格", "items": ["自然写实"], "max": 1},
            "dynamic_stage": "style_discovery",
            "dynamic_stage_label": "风格分析",
            "trace": [],
        }

    monkeypatch.setattr(service, "_run_agent_turn", fake_run_agent_turn)

    first = client.post("/api/v1/inspirations/messages", data={"session_id": session["id"], "text": "想做西安美食和景点攻略"})
    assert first.status_code == 200
    second = client.post("/api/v1/inspirations/messages", data={"session_id": session["id"], "selected_items": "自然写实"})
    assert second.status_code == 200
    third = client.post("/api/v1/inspirations/messages", data={"session_id": session["id"], "action": "confirm_prompt"})
    assert third.status_code == 200
    final = client.post("/api/v1/inspirations/messages", data={"session_id": session["id"], "action": "confirm_allocation_plan"})
    assert final.status_code == 200
    body = final.json()
    assert body["draft"]["locked"] is True
    assert body["draft"]["draft_style_id"] == style["id"]

    result = service.generate_images(session_id=session["id"])
    assert result["status"] == "queued"
