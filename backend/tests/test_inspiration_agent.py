from __future__ import annotations

from typing import Any

from conftest import create_session


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
