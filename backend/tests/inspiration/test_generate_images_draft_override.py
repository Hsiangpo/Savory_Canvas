from __future__ import annotations

from conftest import create_session, setup_model_routing


def test_generate_images_accepts_draft_state_override_and_persists_it(client, monkeypatch):
    session = create_session(client)
    setup_model_routing(client)
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])
    state["style_payload"] = {
        "painting_style": "手绘插画",
        "color_mood": "温暖治愈",
        "prompt_example": "请保持统一风格与清晰图文布局。",
        "style_prompt": "请保持统一风格与清晰图文布局。",
        "extra_keywords": [],
    }
    state["image_count"] = 3
    state["draft_style_id"] = None
    service.inspiration_repo.upsert_state(state)

    scheduled_job_ids: list[str] = []
    monkeypatch.setattr(service.generation_worker, "schedule", lambda job_id: scheduled_job_ids.append(job_id))

    result = service.generate_images(
        session_id=session["id"],
        draft_state={
            "allocation_plan": [
                {"slot_index": 1, "focus_title": "苏州园林主图", "focus_description": "突出园林层次。"},
                {"slot_index": 2, "focus_title": "平江路漫游", "focus_description": "突出水巷动线。"},
                {"slot_index": 3, "focus_title": "苏式点心", "focus_description": "突出点心与器皿。"},
            ],
            "style_prompt": "生成三张苏州旅行手账图。",
            "image_count": 3,
        },
    )

    assert result["status"] == "queued"
    assert scheduled_job_ids
    updated_state = service._ensure_state(session["id"])
    assert len(updated_state["allocation_plan"]) == 3
    assert all(item.get("confirmed") is True for item in updated_state["allocation_plan"])
    assert updated_state["style_prompt"] == "生成三张苏州旅行手账图。"

