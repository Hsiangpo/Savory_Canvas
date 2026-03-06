from __future__ import annotations

import json
from typing import Any

from conftest import create_generation_job, create_session, create_style, setup_model_routing, wait_for_job_end


def test_create_app_uses_legacy_agent_mode_when_cli_flag_present(tmp_path, monkeypatch):
    monkeypatch.setenv("SAVORY_CANVAS_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SAVORY_CANVAS_STORAGE_DIR", str(tmp_path / "storage"))

    import backend.app.main as main_module

    monkeypatch.setattr(main_module.sys, "argv", ["python", "-m", "backend.app.main", "--legacy"])
    app = main_module.create_app()

    assert app.state.services.inspiration.agent_mode == "legacy"
    assert app.state.services.inspiration.creative_agent is None


def test_create_app_does_not_run_port_cleanup_on_boot(tmp_path, monkeypatch):
    monkeypatch.setenv("SAVORY_CANVAS_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SAVORY_CANVAS_STORAGE_DIR", str(tmp_path / "storage"))

    import backend.app.main as main_module

    app = main_module.create_app()

    assert not hasattr(main_module, "_maybe_cleanup_old_backend_processes")
    assert app.state.services.inspiration.agent_mode == "langgraph"


def test_get_inspiration_conversation_includes_langgraph_agent_meta_by_default(client):
    session = create_session(client)

    response = client.get(f"/api/v1/inspirations/{session['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["agent"]["mode"] == "langgraph"
    assert body["agent"]["trace"] == []


def test_inspiration_message_returns_langgraph_agent_meta_when_agent_mode_enabled(client, monkeypatch):
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

    monkeypatch.setattr(service, "agent_mode", "langgraph", raising=False)
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


def test_creative_agent_build_turn_result_maps_generate_images_and_copy():
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent.__new__(CreativeAgent)
    image_result = agent._build_turn_result(
        request={"state": {"stage": "asset_confirming", "locked": False}},
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

    assert image_result["stage"] == "locked"
    assert image_result["locked"] is True
    assert image_result["job_id"] == "job-123"
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


def test_agent_mode_main_path_does_not_call_legacy_handlers_when_agent_succeeds(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration
    service.agent_mode = "langgraph"

    legacy_calls: list[str] = []

    monkeypatch.setattr(service, "_handle_collecting_stage", lambda *args, **kwargs: legacy_calls.append("collecting"))
    monkeypatch.setattr(service, "_handle_prompt_revision", lambda *args, **kwargs: legacy_calls.append("prompt"))
    monkeypatch.setattr(service, "_handle_asset_confirming", lambda *args, **kwargs: legacy_calls.append("asset"))
    monkeypatch.setattr(
        service,
        "_run_agent_turn",
        lambda **kwargs: {
            "reply": "Agent 主脑接管成功。",
            "stage": "prompt_revision",
            "locked": False,
            "style_payload": {
                "painting_style": "自然写实",
                "color_mood": "暖色",
                "prompt_example": "请保持统一风格。",
                "style_prompt": "生成一张：西安城墙攻略图。",
                "extra_keywords": [],
            },
            "style_prompt": "生成一张：西安城墙攻略图。",
            "dynamic_stage": "prompt_generation",
            "dynamic_stage_label": "提示词生成",
            "trace": [],
        },
    )

    response = client.post(
        "/api/v1/inspirations/messages",
        data={"session_id": session["id"], "text": "西安城墙和羊肉泡馍，帮我先推进"},
    )

    assert response.status_code == 200
    assert legacy_calls == []


def test_agent_mode_main_path_does_not_call_legacy_use_style_profile_when_agent_succeeds(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration
    service.agent_mode = "langgraph"

    monkeypatch.setattr(service, "_handle_use_style_profile", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy use_style_profile should not run")))
    monkeypatch.setattr(
        service,
        "_run_agent_turn",
        lambda **kwargs: {
            "reply": "Agent 已接管风格模板应用。",
            "stage": "prompt_revision",
            "locked": False,
            "trace": [],
        },
    )

    response = client.post(
        "/api/v1/inspirations/messages",
        data={
            "session_id": session["id"],
            "action": "use_style_profile",
            "selected_items": "style-id",
        },
    )

    assert response.status_code == 200


def test_agent_mode_main_path_does_not_call_legacy_locked_handler_when_agent_succeeds(client, monkeypatch):
    session = create_session(client)
    service = client.app.state.services.inspiration
    service.agent_mode = "langgraph"

    state = service._ensure_state(session["id"])
    state["locked"] = True
    state["stage"] = "locked"
    service.inspiration_repo.upsert_state(state)

    monkeypatch.setattr(service, "_handle_locked_stage", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy locked handler should not run")))
    monkeypatch.setattr(
        service,
        "_run_agent_turn",
        lambda **kwargs: {
            "reply": "Agent 已处理锁定态后续动作。",
            "stage": "locked",
            "locked": True,
            "trace": [],
        },
    )

    response = client.post(
        "/api/v1/inspirations/messages",
        data={"session_id": session["id"], "action": "skip_save"},
    )

    assert response.status_code == 200


def test_creative_agent_graph_supports_multiple_tool_calls_before_final_response(tmp_path, monkeypatch):
    monkeypatch.setenv("SAVORY_CANVAS_DB_PATH", str(tmp_path / "agent.db"))
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent.__new__(CreativeAgent)
    agent.db_path = tmp_path / "agent.db"
    agent.tools = {
        "extract_assets": type("Tool", (), {"invoke": staticmethod(lambda args: {"foods": ["羊肉泡馍"], "scenes": ["城墙"]})})(),
        "allocate_assets_to_images": type("Tool", (), {"invoke": staticmethod(lambda args: [{"slot_index": 1, "focus_title": "城墙主图", "focus_description": "突出城墙", "source_asset_ids": ["asset-1"], "confirmed": False}])})(),
    }

    decisions = iter(
        [
            {"decision": "use_tool", "tool_name": "extract_assets", "tool_args": {}, "dynamic_stage": "asset_extraction", "dynamic_stage_label": "资产提取"},
            {"decision": "use_tool", "tool_name": "allocate_assets_to_images", "tool_args": {}, "dynamic_stage": "allocation", "dynamic_stage_label": "分图策划"},
            {"decision": "respond_directly", "result": {"reply": "已完成多步规划。", "stage": "asset_confirming", "locked": False}},
        ]
    )

    def fake_agent_node(state):
        decision = next(decisions)
        trace = list(state.get("trace") or [])
        result = decision.get("result") if decision["decision"] == "respond_directly" else None
        return {"decision": decision, "result": result, "agent_messages": [], "trace": trace}

    agent._agent_node = fake_agent_node
    agent.__post_init__()
    turn = agent.respond(
        {
            "session_id": "session-1",
            "text": "请帮我做西安攻略",
            "state": {"stage": "style_collecting", "locked": False},
            "attachments": [],
            "selected_items": [],
        }
    )

    assert turn["reply"] == "已完成多步规划。"
    assert turn["stage"] == "asset_confirming"


def test_agent_mode_end_to_end_can_lock_and_generate_without_legacy_handlers(client, monkeypatch):
    setup_model_routing(client)
    session = create_session(client, title="agent主脑流程", content_mode="food_scenic")
    style = create_style(client, session["id"], {"painting_style": "自然写实"})
    service = client.app.state.services.inspiration
    service.agent_mode = "langgraph"

    monkeypatch.setattr(service, "_handle_collecting_stage", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy collecting should not run")))
    monkeypatch.setattr(service, "_handle_prompt_revision", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy prompt should not run")))
    monkeypatch.setattr(service, "_handle_asset_confirming", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy asset should not run")))

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
                "options": {"title": "请选择下一步", "items": ["暂不保存"], "max": 1},
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

    job = create_generation_job(client, session["id"], body["draft"]["draft_style_id"], image_count=1)
    ended = wait_for_job_end(client, job["id"])
    assert ended["status"] in {"success", "partial_success"}


def test_legacy_mode_flow_can_lock_and_generate(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="legacy流程", content_mode="food")
    service = client.app.state.services.inspiration
    service.agent_mode = "legacy"
    service.creative_agent = None

    from backend.app.services.style_service import StyleService

    def finish_collecting(_self, **_kwargs):
        return {
            "reply": "已完成风格收集",
            "options": {"title": "请选择生成数量", "items": ["1"], "max": 1},
            "stage": "image_count",
            "next_stage": "",
            "is_finished": True,
            "fallback_used": False,
        }

    def fake_call_text_model(_self, _provider, _model_name, system_prompt, user_prompt, *, strict_json):
        if "参数提取助手" in system_prompt:
            return '{"image_count": 1}'
        if "提示词质检助手" in system_prompt:
            return "READY"
        if "资产提取助手" in system_prompt:
            return '{"locations":["西安"],"scenes":["城墙"],"foods":["羊肉泡馍"],"keywords":["西安攻略"],"confidence":0.9}'
        if "分图策划助手" in system_prompt:
            source_asset_ids: list[str] = []
            marker = "可用 source_asset_ids："
            if marker in user_prompt:
                source_line = user_prompt.split(marker, 1)[1].splitlines()[0]
                source_asset_ids = [item.strip() for item in source_line.split(",") if item.strip()]
            source_id = source_asset_ids[0] if source_asset_ids else "asset-source-1"
            return json.dumps(
                {
                    "items": [
                        {
                            "slot_index": 1,
                            "focus_title": "城墙与泡馍",
                            "focus_description": "图解西安城墙与羊肉泡馍。",
                            "locations": ["西安"],
                            "scenes": ["城墙"],
                            "foods": ["羊肉泡馍"],
                            "keywords": ["西安攻略"],
                            "source_asset_ids": [source_id],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        return "生成一张：西安城墙与羊肉泡馍攻略图。"

    monkeypatch.setattr(StyleService, "chat", finish_collecting)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)

    client.post("/api/v1/assets/text", json={"session_id": session["id"], "asset_type": "text", "content": "西安城墙和羊肉泡馍"})
    first = client.post("/api/v1/inspirations/messages", data={"session_id": session["id"], "text": "请帮我做一篇西安攻略"})
    assert first.status_code == 200
    second = client.post("/api/v1/inspirations/messages", data={"session_id": session["id"], "selected_items": "1"})
    assert second.status_code == 200
    third = client.post("/api/v1/inspirations/messages", data={"session_id": session["id"], "action": "confirm_prompt"})
    assert third.status_code == 200
    final = client.post("/api/v1/inspirations/messages", data={"session_id": session["id"], "action": "confirm_allocation_plan"})
    assert final.status_code == 200
    body = final.json()
    assert body["draft"]["locked"] is True

    job = create_generation_job(client, session["id"], body["draft"]["draft_style_id"], image_count=1)
    ended = wait_for_job_end(client, job["id"])
    assert ended["status"] in {"success", "partial_success"}

