from __future__ import annotations

from io import BytesIO

from conftest import create_session, setup_model_routing


def _post_inspiration_message(client, *, data: list[tuple[str, str]], files: list[tuple[str, tuple[str, BytesIO, str]]]):
    payload: dict[str, str | list[str]] = {}
    for key, value in data:
        current = payload.get(key)
        if current is None:
            payload[key] = value
            continue
        if isinstance(current, list):
            current.append(value)
            continue
        payload[key] = [current, value]
    return client.post(
        "/api/v1/inspirations/messages",
        data=payload,
        files=files or None,
    )


def _fake_asset_extract_payload() -> str:
    return (
        '{"locations":["西安"],"scenes":["钟楼","华清池"],'
        '"foods":["羊肉泡馍","肉夹馍"],"keywords":["西安美食","城市路线"],"confidence":0.91}'
    )


def _fake_allocation_plan_payload() -> str:
    return (
        '{"items":['
        '{"slot_index":1,"focus_title":"景点主图","focus_description":"聚焦西安钟楼与城市动线。","locations":["西安"],"scenes":["钟楼"],"foods":[],"keywords":["路线"],"source_asset_ids":["invalid-source"]},'
        '{"slot_index":2,"focus_title":"美食主图","focus_description":"聚焦肉夹馍与冰峰饮品细节。","locations":["西安"],"scenes":[],"foods":["肉夹馍","冰峰"],"keywords":["美食"],"source_asset_ids":["invalid-source"]}'
        ']}'
    )


def _prepare_locked_conversation(client, monkeypatch):
    setup_model_routing(client)
    session = create_session(client, title="锁定草案会话")

    from backend.app.services.style_service import StyleService

    def finish_collecting(_self, **_kwargs):
        return {
            "reply": "风格收集已完成",
            "options": {"title": "请选择生成数量", "items": ["1", "2"], "max": 1},
            "stage": "image_count",
            "next_stage": "",
            "is_finished": True,
            "fallback_used": False,
        }

    def fake_call_text_model(_self, _provider, _model_name, system_prompt, _user_prompt, *, strict_json):
        if "资产提取助手" in system_prompt:
            return _fake_asset_extract_payload()
        if "分图策划助手" in system_prompt:
            return _fake_allocation_plan_payload()
        if "READY" in system_prompt:
            return "READY"
        if strict_json:
            return "READY"
        return (
            "生成一张统一风格图片，聚焦美食细节。\n\n"
            "生成一张统一风格图片，聚焦城市路线与景点导览。"
        )

    monkeypatch.setattr(StyleService, "chat", finish_collecting)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)
    enter_prompt = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "2"),
            ("text", "我想做西安双图内容"),
        ],
        files=[],
    )
    assert enter_prompt.status_code == 200
    assert enter_prompt.json()["draft"]["stage"] == "prompt_revision"

    confirm_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("action", "confirm_prompt"),
            ("selected_items", "确定使用"),
        ],
        files=[],
    )
    assert confirm_response.status_code == 200
    confirm_body = confirm_response.json()
    assert confirm_body["draft"]["stage"] == "asset_confirming"
    assert confirm_body["draft"]["locked"] is False
    assert "asset_candidates" in confirm_body["messages"][-1]

    lock_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("action", "confirm_assets"),
            ("selected_items", "确认资产并锁定"),
        ],
        files=[],
    )
    assert lock_response.status_code == 200
    lock_body = lock_response.json()
    assert lock_body["draft"]["stage"] == "locked"
    assert lock_body["draft"]["locked"] is True
    assert lock_body["draft"]["draft_style_id"]
    return session["id"]


def test_inspiration_draft_locked_blocks_further_modification(client, monkeypatch):
    session_id = _prepare_locked_conversation(client, monkeypatch)

    after_locked = _post_inspiration_message(
        client,
        data=[
            ("session_id", session_id),
            ("text", "我还想再改一版"),
            ("action", "continue"),
        ],
        files=[],
    )
    assert after_locked.status_code == 200
    body = after_locked.json()
    assert body["draft"]["stage"] == "locked"
    assert body["draft"]["locked"] is True
    assert "已锁定" in body["messages"][-1]["content"]


def test_inspiration_locked_save_style_action(client, monkeypatch):
    session_id = _prepare_locked_conversation(client, monkeypatch)

    before_styles = client.get("/api/v1/styles")
    assert before_styles.status_code == 200
    before_count = len(before_styles.json()["items"])

    response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session_id),
            ("action", "save_style"),
            ("selected_items", "保存风格"),
        ],
        files=[],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["stage"] == "locked"
    assert body["draft"]["locked"] is True
    assert "已保存风格参数和提示词" in body["messages"][-1]["content"]

    after_styles = client.get("/api/v1/styles")
    assert after_styles.status_code == 200
    assert len(after_styles.json()["items"]) == before_count + 1


def test_inspiration_locked_skip_save_action(client, monkeypatch):
    session_id = _prepare_locked_conversation(client, monkeypatch)

    before_styles = client.get("/api/v1/styles")
    assert before_styles.status_code == 200
    before_count = len(before_styles.json()["items"])

    response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session_id),
            ("action", "skip_save"),
            ("selected_items", "暂不保存"),
        ],
        files=[],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["stage"] == "locked"
    assert body["draft"]["locked"] is True
    assert "已跳过保存" in body["messages"][-1]["content"]

    after_styles = client.get("/api/v1/styles")
    assert after_styles.status_code == 200
    assert len(after_styles.json()["items"]) == before_count
