from __future__ import annotations

from pathlib import Path


def test_creative_agent_redirects_recommend_city_combo_to_dedicated_tool(tmp_path):
    from backend.app.agent.creative_agent import CreativeAgent
    from backend.tests.test_inspiration_agent import _FakeLLMProvider, _ToolStub

    decisions = [
        {
            "decision": "use_tool",
            "reason": "用户想先看推荐组合。",
            "tool_name": "suggest_painting_style",
            "tool_args": {"session_id": "session-1"},
        },
        {
            "decision": "respond_directly",
            "reason": "基于推荐组合给用户选择。",
            "result": {
                "reply": "我先给你两套方向：一套苏州园林配苏式点心，一套扬州园林配早茶。你更想走哪一条？",
                "stage": "style_selected_waiting_content",
                "locked": False,
                "progress": 28,
                "progress_label": "风格已选，待定内容",
                "options": {"items": [{"label": "先看苏州这套", "action_hint": "choose_combo_1"}]},
            },
        },
    ]
    observed = {"suggest": 0, "recommend": 0}
    agent = CreativeAgent(
        llm_provider=_FakeLLMProvider(decisions),
        tools={
            "suggest_painting_style": _ToolStub(lambda _: observed.__setitem__("suggest", observed["suggest"] + 1) or {"reply": "bad"}),
            "recommend_city_content_combos": _ToolStub(lambda _: observed.__setitem__("recommend", observed["recommend"] + 1) or {
                "items": [
                    {"city": "苏州", "scenes": ["拙政园"], "foods": ["苏式点心"], "summary": "园林漫游 + 细腻甜点"},
                    {"city": "扬州", "scenes": ["瘦西湖"], "foods": ["扬州早茶"], "summary": "园林水岸 + 早茶烟火"},
                ]
            }),
        },
        db_path=tmp_path / "agent-recommend-city.db",
    )

    result = agent.respond(
        {
            "session_id": "session-1",
            "text": "",
            "selected_items": ["先给我推荐两套城市内容组合"],
            "attachments": [],
            "action": "recommend_city_combo",
            "state": {
                "stage": "style_selected_waiting_content",
                "style_stage": "painting_style",
                "locked": False,
                "style_payload": {},
                "style_prompt": "",
                "asset_candidates": {},
                "allocation_plan": [],
                "content_mode": "food_scenic",
            },
        }
    )

    assert observed["suggest"] == 0
    assert observed["recommend"] == 1
    assert result["stage"] == "style_selected_waiting_content"
    assert "两套方向" in result["reply"]

