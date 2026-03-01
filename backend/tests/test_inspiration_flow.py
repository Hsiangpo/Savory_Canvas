from __future__ import annotations

import json
from io import BytesIO

from conftest import create_session, create_style, setup_model_routing, wait_until


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

def _fake_allocation_plan_payload(user_prompt: str = "") -> str:
    marker = "可用 source_asset_ids："
    source_asset_ids: list[str] = []
    if marker in user_prompt:
        source_asset_ids = [raw_id.strip() for raw_id in user_prompt.split(marker, 1)[1].splitlines()[0].replace("，", ",").split(",") if raw_id.strip()]
    if not source_asset_ids:
        source_asset_ids = ["asset-source-1", "asset-source-2"]
    first_asset_id = source_asset_ids[0]
    second_asset_id = source_asset_ids[1] if len(source_asset_ids) > 1 else first_asset_id
    return (
        '{"items":['
        f'{{"slot_index":1,"focus_title":"景点主图","focus_description":"聚焦西安钟楼与城市动线。","locations":["西安"],"scenes":["钟楼"],"foods":[],"keywords":["路线"],"source_asset_ids":["{first_asset_id}"]}},'
        f'{{"slot_index":2,"focus_title":"美食主图","focus_description":"聚焦肉夹馍与冰峰饮品细节。","locations":["西安"],"scenes":[],"foods":["肉夹馍","冰峰"],"keywords":["美食"],"source_asset_ids":["{second_asset_id}"]}}'
        ']}'
    )

def test_inspiration_allocation_plan_generated_and_confirmed_when_locking(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="分图确认会话")

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
        if "参数提取助手" in system_prompt:
            if "两张" in _user_prompt or "2张" in _user_prompt:
                return '{"image_count": 2}'
            if "一张" in _user_prompt or "1张" in _user_prompt:
                return '{"image_count": 1}'
            return '{"image_count": null}'
        if "资产提取助手" in system_prompt:
            return _fake_asset_extract_payload()
        if "分图策划助手" in system_prompt:
            return _fake_allocation_plan_payload(_user_prompt)
        if "READY" in system_prompt:
            return "READY"
        if strict_json:
            return '{"image_count": null}'
        return (
            "生成一张西安景点主题图解，聚焦钟楼与路线导览。\n\n"
            "生成一张西安美食主题图解，聚焦肉夹馍与冰峰。"
        )

    monkeypatch.setattr(StyleService, "chat", finish_collecting)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)

    enter_prompt = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "2"),
        ],
        files=[],
    )
    assert enter_prompt.status_code == 200
    assert enter_prompt.json()["draft"]["stage"] == "prompt_revision"

    confirm_prompt = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("action", "confirm_prompt"),
            ("selected_items", "确认提示词"),
            ("text", "我想做西安，两张图，一张景点，一张美食。"),
        ],
        files=[],
    )
    assert confirm_prompt.status_code == 200
    confirm_body = confirm_prompt.json()
    allocation_plan = confirm_body["draft"].get("allocation_plan") or []
    assert confirm_body["draft"]["stage"] == "asset_confirming"
    assert len(allocation_plan) == 2
    assert all(item["confirmed"] is False for item in allocation_plan)

    lock_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("action", "confirm_assets"),
            ("selected_items", "确认分图并锁定"),
        ],
        files=[],
    )
    assert lock_response.status_code == 200
    lock_body = lock_response.json()
    assert lock_body["draft"]["locked"] is True
    assert lock_body["draft"]["stage"] == "locked"
    assert all(item["confirmed"] is True for item in lock_body["draft"].get("allocation_plan") or [])


def test_get_inspiration_conversation_initializes_welcome(client):
    session = create_session(client, title="灵感欢迎语会话")
    response = client.get(f"/api/v1/inspirations/{session['id']}")
    assert response.status_code == 200
    body = response.json()

    assert body["session_id"] == session["id"]
    assert body["draft"]["stage"] == "style_collecting"
    assert body["draft"]["locked"] is False
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "assistant"
    assert "欢迎来到 Savory Canvas" in body["messages"][0]["content"]


def test_inspiration_supports_mixed_input_and_stage_progress(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="混合输入会话")

    from backend.app.services.style_service import StyleService

    def fake_chat(_self, *, stage, selected_items, **_kwargs):
        if stage == "painting_style":
            return {
                "reply": "进入背景装饰阶段",
                "options": {"title": "请选择背景装饰", "items": ["暖光餐桌", "窗边光影"], "max": 2},
                "stage": "background_decor",
                "next_stage": "color_mood",
                "is_finished": False,
                "fallback_used": False,
            }
        if stage == "background_decor":
            return {
                "reply": "进入色彩情绪阶段",
                "options": {"title": "请选择色彩情绪", "items": ["暖金氛围", "复古棕调"], "max": 2},
                "stage": "color_mood",
                "next_stage": "image_count",
                "is_finished": False,
                "fallback_used": False,
            }
        if stage == "color_mood":
            return {
                "reply": "请选择生成数量",
                "options": {"title": "请选择生成数量", "items": ["1", "2", "3"], "max": 1},
                "stage": "image_count",
                "next_stage": "",
                "is_finished": False,
                "fallback_used": False,
            }
        return {
            "reply": "风格收集完成",
            "options": {"title": "请选择生成数量", "items": ["1", "2", "3"], "max": 1},
            "stage": "image_count",
            "next_stage": "",
            "is_finished": bool(selected_items and selected_items[0].isdigit()),
            "fallback_used": False,
        }

    monkeypatch.setattr(StyleService, "chat", fake_chat)
    monkeypatch.setattr(
        StyleService,
        "_call_text_model",
        lambda _self, _provider, _model_name, system_prompt, _user_prompt, *, strict_json: (
            _fake_asset_extract_payload()
            if "资产提取助手" in system_prompt
            else (
                "READY"
                if "READY" in system_prompt
                else (
                    "生成一张法式晚宴主题图片，突出美食细节与菜品质感。\n\n"
                    "生成一张法式晚宴主题图片，突出空间氛围与动线叙事。"
                )
            )
        ),
    )

    first_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("text", "我想做法式晚宴主题"),
            ("selected_items", "油画厚涂"),
            ("selected_items", "电影写实"),
        ],
        files=[
            ("images", ("scene.png", BytesIO(b"fake-image"), "image/png")),
            ("videos", ("scene.mp4", BytesIO(b"fake-video"), "video/mp4")),
        ],
    )
    assert first_response.status_code == 200
    first_body = first_response.json()
    assert first_body["draft"]["stage"] == "style_collecting"
    user_messages = [msg for msg in first_body["messages"] if msg["role"] == "user"]
    assert user_messages
    attachment_types = {item["type"] for item in user_messages[-1]["attachments"]}
    assert {"text", "image", "video"}.issubset(attachment_types)
    image_attachments = [item for item in user_messages[-1]["attachments"] if item["type"] == "image"]
    assert image_attachments
    assert image_attachments[0]["preview_url"].startswith("http://127.0.0.1:8887/static/images/")

    _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "暖光餐桌"),
            ("selected_items", "窗边光影"),
        ],
        files=[],
    )
    _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "暖金氛围"),
            ("selected_items", "复古棕调"),
        ],
        files=[],
    )
    final_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "2"),
        ],
        files=[],
    )
    assert final_response.status_code == 200
    final_body = final_response.json()
    assert final_body["draft"]["stage"] == "prompt_revision"
    assert final_body["draft"]["image_count"] == 2
    assert final_body["draft"]["locked"] is False

    def _poll_transcript_message():
        response = client.get(f"/api/v1/inspirations/{session['id']}")
        if response.status_code != 200:
            return None
        body = response.json()
        for msg in body["messages"]:
            for attachment in msg.get("attachments", []):
                if attachment["type"] == "transcript":
                    return body
        return None

    transcript_body = wait_until(_poll_transcript_message, timeout=6.0, interval=0.1)
    assert transcript_body is not None


def test_inspiration_image_requires_vision_model(client):
    setup_model_routing(client, text_model_name="gpt-4.1-mini")
    session = create_session(client, title="视觉能力校验")

    response = _post_inspiration_message(
        client,
        data=[("session_id", session["id"])],
        files=[("images", ("need-vision.png", BytesIO(b"fake-image"), "image/png"))],
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "E-1010"
    assert "不支持图片解析" in body["message"]


def test_inspiration_asset_candidates_focus_location_scene_food(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="资产分类校验")

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
        if "资产提取助手" in system_prompt or "图片资产解析助手" in system_prompt:
            return _fake_asset_extract_payload()
        if "分图策划助手" in system_prompt:
            return _fake_allocation_plan_payload(_user_prompt)
        if "READY" in system_prompt:
            return "READY"
        return (
            "生成一张统一风格图片，聚焦城市地标细节。\n\n"
            "生成一张统一风格图片，聚焦美食细节。"
        )

    monkeypatch.setattr(StyleService, "chat", finish_collecting)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)

    enter_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "2"),
        ],
        files=[("images", ("style-ref.png", BytesIO(b"style-image"), "image/png"))],
    )
    assert enter_response.status_code == 200
    enter_body = enter_response.json()
    user_messages = [msg for msg in enter_body["messages"] if msg["role"] == "user"]
    assert user_messages
    style_ref_attachment = next(item for item in user_messages[-1]["attachments"] if item["type"] == "image")
    style_ref_asset_id = style_ref_attachment["asset_id"]

    confirm_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("action", "confirm_prompt"),
            ("selected_items", "确定使用"),
            ("text", "我想做陕西西安路线，景点包括钟楼、华清池、兵马俑，美食包含biangbiang面、肉夹馍、冰峰、羊肉泡馍。"),
        ],
        files=[],
    )
    assert confirm_response.status_code == 200, confirm_response.text
    confirm_body = confirm_response.json()
    assert confirm_body["draft"]["stage"] == "asset_confirming"
    candidates = confirm_body["messages"][-1]["asset_candidates"]
    assert any("西安" in item for item in candidates["locations"])
    assert any("钟楼" in item or "华清池" in item for item in candidates["scenes"])
    assert any("肉夹馍" in item or "biangbiang面" in item for item in candidates["foods"])
    assert any("西安" in item for item in candidates["keywords"])
    assert style_ref_asset_id in candidates["source_asset_ids"]


def test_inspiration_asset_candidates_food_only_clears_scene_candidates(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="资产分类仅美食校验")

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
        if "资产提取助手" in system_prompt or "图片资产解析助手" in system_prompt:
            return (
                '{"locations":["河西走廊"],"scenes":[],'
                '"foods":["烤羊","焖面","沙葱包子","冰煮羊"],"keywords":["河西走廊美食"],"confidence":0.92}'
            )
        if "分图策划助手" in system_prompt:
            return _fake_allocation_plan_payload(_user_prompt)
        if "READY" in system_prompt:
            return "READY"
        return (
            "生成一张统一风格图片，聚焦美食细节。\n\n"
            "生成一张统一风格图片，聚焦食材特写。"
        )

    monkeypatch.setattr(StyleService, "chat", finish_collecting)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)

    enter_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "2"),
        ],
        files=[],
    )
    assert enter_response.status_code == 200
    assert enter_response.json()["draft"]["stage"] == "prompt_revision"

    confirm_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("action", "confirm_prompt"),
            ("selected_items", "确定使用"),
            ("text", "我只做河西走廊美食攻略，主打烤羊、焖面、沙葱包子和冰煮羊，不要景点内容。"),
        ],
        files=[],
    )
    assert confirm_response.status_code == 200, confirm_response.text
    confirm_body = confirm_response.json()
    candidates = confirm_body["messages"][-1]["asset_candidates"]
    assert any("河西走廊" in item for item in candidates["locations"])
    assert candidates["scenes"] == []
    assert any("羊" in item or "焖面" in item for item in candidates["foods"])
    assert candidates["keywords"]
    assert not any("钟楼" in item or "华清池" in item for item in candidates["keywords"])


def test_inspiration_allocation_plan_backfills_location_from_candidates(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="分图地点回填校验")

    from backend.app.services.style_service import StyleService

    def finish_collecting(_self, **_kwargs):
        return {
            "reply": "风格收集已完成",
            "options": {"title": "请选择生成数量", "items": ["1"], "max": 1},
            "stage": "image_count",
            "next_stage": "",
            "is_finished": True,
            "fallback_used": False,
        }

    def fake_call_text_model(_self, _provider, _model_name, system_prompt, user_prompt, *, strict_json):
        if "资产提取助手" in system_prompt:
            return (
                '{"locations":["河西走廊"],"scenes":[],' 
                '"foods":["烤羊","焖面"],"keywords":["西北美食"],"confidence":0.9}'
            )
        if "分图策划助手" in system_prompt:
            marker = "可用 source_asset_ids："
            source_asset_id = "asset-source-1"
            if marker in user_prompt:
                source_asset_id = user_prompt.split(marker, 1)[1].splitlines()[0].replace("，", ",").split(",")[0].strip() or source_asset_id
            return (
                '{"items":['
                f'{{"slot_index":1,"focus_title":"美食主图","focus_description":"聚焦河西走廊特色美食。","locations":[],"scenes":[],"foods":["烤羊","焖面"],"keywords":["西北美食"],"source_asset_ids":["{source_asset_id}"]}}'
                ']}'
            )
        if "READY" in system_prompt:
            return "READY"
        return "生成一张河西走廊美食主题图解，聚焦烤羊与焖面。"

    monkeypatch.setattr(StyleService, "chat", finish_collecting)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)

    enter_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "1"),
        ],
        files=[],
    )
    assert enter_response.status_code == 200
    assert enter_response.json()["draft"]["stage"] == "prompt_revision"

    confirm_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("action", "confirm_prompt"),
            ("selected_items", "确定使用"),
            ("text", "我只做河西走廊美食，不做景点。"),
        ],
        files=[],
    )
    assert confirm_response.status_code == 200
    plan = confirm_response.json()["draft"]["allocation_plan"]
    assert plan
    assert "河西走廊" in plan[0]["locations"]


def test_inspiration_confirm_prompt_with_feedback_refreshes_prompt(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="确认时反馈刷新提示词")

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

    def fake_call_text_model(_self, _provider, _model_name, system_prompt, user_prompt, *, strict_json):
        if "资产提取助手" in system_prompt or "图片资产解析助手" in system_prompt:
            return _fake_asset_extract_payload()
        if "分图策划助手" in system_prompt:
            return _fake_allocation_plan_payload(user_prompt)
        if "READY" in system_prompt:
            return "READY"
        if "用户硬性要求" in user_prompt and "西安" in user_prompt:
            return (
                "生成一张西安景点主题图解，聚焦钟楼、华清池、兵马俑和浏览路线。\n\n"
                "生成一张西安美食主题图解，聚焦biangbiang面、肉夹馍、冰峰、羊肉泡馍。"
            )
        return (
            "生成一张示例风格图，突出复古手账材质。\n\n"
            "生成一张示例风格图，突出拼贴装饰。"
        )

    monkeypatch.setattr(StyleService, "chat", finish_collecting)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)

    enter_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "2"),
        ],
        files=[("images", ("style-ref.png", BytesIO(b"style-image"), "image/png"))],
    )
    assert enter_response.status_code == 200
    assert enter_response.json()["draft"]["stage"] == "prompt_revision"

    confirm_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("action", "confirm_prompt"),
            ("selected_items", "确定使用"),
            ("text", "我想做陕西西安路线，景点包括钟楼、华清池、兵马俑，美食包含biangbiang面、肉夹馍、冰峰、羊肉泡馍。"),
        ],
        files=[],
    )
    assert confirm_response.status_code == 200
    body = confirm_response.json()
    assert body["draft"]["stage"] == "asset_confirming"
    style_prompt = body["draft"]["style_payload"]["style_prompt"]
    assert "西安" in style_prompt
    assert "钟楼" in style_prompt
    assert "肉夹馍" in style_prompt
    assert "示例风格图" not in style_prompt


def test_inspiration_content_image_contributes_to_style_prompt(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="内容图语义注入校验")

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

    def fake_call_text_model_with_images(
        _self,
        _provider,
        _model_name,
        system_prompt,
        _user_prompt,
        image_urls,
        *,
        strict_json,
    ):
        assert image_urls
        if "图片资产解析助手" in system_prompt:
            assert strict_json is True
            return (
                '{"locations":["西安"],"scenes":["钟楼","兵马俑"],'
                '"foods":["肉夹馍","羊肉泡馍"],"keywords":["古城路线"],"confidence":0.93}'
            )
        if "分图策划助手" in system_prompt:
            return _fake_allocation_plan_payload(_user_prompt)
        if "资深视觉创意总监" in system_prompt:
            assert strict_json is False
            return (
                "生成一张西安景点主题图解，聚焦钟楼与兵马俑路线。\n\n"
                "生成一张西安美食主题图解，聚焦肉夹馍与羊肉泡馍。"
            )
        return '{"locations":[],"scenes":[],"foods":[],"keywords":[],"confidence":0.8}'

    def fake_call_text_model(_self, _provider, _model_name, system_prompt, user_prompt, *, strict_json):
        if "READY" in system_prompt:
            return "READY"
        if "资产提取助手" in system_prompt:
            return _fake_asset_extract_payload()
        if "分图策划助手" in system_prompt:
            return _fake_allocation_plan_payload(user_prompt)
        if "用户硬性要求" in user_prompt and "钟楼" in user_prompt and "肉夹馍" in user_prompt:
            return (
                "生成一张西安景点主题图解，聚焦钟楼与兵马俑路线。\n\n"
                "生成一张西安美食主题图解，聚焦肉夹馍与羊肉泡馍。"
            )
        return (
            "生成一张示例风格图，突出复古手账材质。\n\n"
            "生成一张示例风格图，突出拼贴装饰。"
        )

    monkeypatch.setattr(StyleService, "chat", finish_collecting)
    monkeypatch.setattr(StyleService, "_call_text_model_with_images", fake_call_text_model_with_images)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)

    enter_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "2"),
        ],
        files=[("images", ("content-asset.png", BytesIO(b"content-image"), "image/png"))],
    )
    assert enter_response.status_code == 200
    body = enter_response.json()
    assert body["draft"]["stage"] == "prompt_revision"
    style_prompt = body["draft"]["style_payload"]["style_prompt"]
    assert "钟楼" in style_prompt
    assert "肉夹馍" in style_prompt


def test_inspiration_prompt_revision_preserves_user_assets_on_followup_feedback(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="修订保持用户资产")

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

    def fake_call_text_model(_self, _provider, _model_name, system_prompt, user_prompt, *, strict_json):
        if "资产提取助手" in system_prompt:
            return _fake_asset_extract_payload()
        if "分图策划助手" in system_prompt:
            return _fake_allocation_plan_payload(user_prompt)
        if "READY" in system_prompt:
            return "READY"
        if "用户硬性要求：" in user_prompt and "西安" in user_prompt:
            return (
                "生成一张西安景点主题图解，聚焦钟楼、华清池与兵马俑路线。\n\n"
                "生成一张西安美食主题图解，聚焦biangbiang面、肉夹馍、冰峰与羊肉泡馍。"
            )
        return (
            "生成一张特色街头小吃图解，突出手账装饰。\n\n"
            "生成一张惬意下午茶甜品图解，突出纸张纹理。"
        )

    monkeypatch.setattr(StyleService, "chat", finish_collecting)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)

    enter_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "2"),
        ],
        files=[],
    )
    assert enter_response.status_code == 200
    assert enter_response.json()["draft"]["stage"] == "prompt_revision"

    first_revise = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("text", "我想做陕西西安路线，景点包括钟楼、华清池、兵马俑，美食包含biangbiang面、肉夹馍、冰峰、羊肉泡馍。"),
        ],
        files=[],
    )
    assert first_revise.status_code == 200

    followup_revise = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("text", "这种风格的对吧？"),
        ],
        files=[],
    )
    assert followup_revise.status_code == 200
    body = followup_revise.json()
    style_prompt = body["draft"]["style_payload"]["style_prompt"]
    assert "西安" in style_prompt
    assert "肉夹馍" in style_prompt
    assert "下午茶" not in style_prompt


def test_inspiration_prompt_fallback_not_expose_style_payload_dict(client, monkeypatch):
    setup_model_routing(client)
    session = create_session(client, title="提示词降级格式校验")

    from backend.app.services.style_service import StyleService

    def fake_chat(_self, *, stage, **_kwargs):
        if stage == "painting_style":
            return {
                "reply": "进入数量选择阶段",
                "options": {"title": "请选择生成数量", "items": ["1", "2"], "max": 1},
                "stage": "painting_style",
                "next_stage": "image_count",
                "is_finished": False,
                "fallback_used": False,
            }
        return {
            "reply": "风格收集完成",
            "options": {"title": "请选择生成数量", "items": ["1", "2"], "max": 1},
            "stage": "image_count",
            "next_stage": "",
            "is_finished": True,
            "fallback_used": False,
        }

    monkeypatch.setattr(StyleService, "chat", fake_chat)
    monkeypatch.setattr(StyleService, "_call_text_model", lambda *_args, **_kwargs: '{"demo":"json"}')

    first_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "手绘插画"),
        ],
        files=[],
    )
    assert first_response.status_code == 200

    second_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "2"),
        ],
        files=[],
    )
    assert second_response.status_code == 503
    second_body = second_response.json()
    assert second_body["code"] == "E-1004"
    assert "模型输出格式异常" in second_body["message"]


def test_inspiration_multi_image_prompt_requires_split_blocks(client, monkeypatch):
    setup_model_routing(client)
    session = create_session(client, title="分图提示词格式校验")

    from backend.app.services.style_service import StyleService

    def fake_chat(_self, **_kwargs):
        return {
            "reply": "风格收集完成",
            "options": {"title": "请选择生成数量", "items": ["1", "2"], "max": 1},
            "stage": "image_count",
            "next_stage": "",
            "is_finished": True,
            "fallback_used": False,
        }

    def fake_call_text_model(_self, _provider, _model_name, system_prompt, _user_prompt, *, strict_json):
        if "READY" in system_prompt:
            return "READY"
        if "分图策划助手" in system_prompt:
            return _fake_allocation_plan_payload(_user_prompt)
        if "更专业、更可执行的中文母提示词" in system_prompt:
            return (
                "生成一张西安景点主题的复古旅行手账风格竖版图解海报，重点描绘钟楼、华清池、兵马俑。\n\n"
                "生成一张西安美食主题的复古手账风格竖版图解海报，重点描绘biangbiang面、肉夹馍、冰峰、羊肉泡馍。"
            )
        return "生成两张复古旅行手账风格的竖版图解海报，第一张景点，第二张美食。"

    monkeypatch.setattr(StyleService, "chat", fake_chat)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)

    response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "2"),
        ],
        files=[],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["stage"] == "prompt_revision"
    assert body["draft"]["image_count"] == 2
    style_prompt = body["draft"]["style_payload"]["style_prompt"]
    assert "生成两张" not in style_prompt
    assert style_prompt.count("生成一张") >= 2


def test_inspiration_prompt_revision_text_overrides_existing_image_count(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="张数覆盖会话")

    from backend.app.services.style_service import StyleService

    def fake_chat(_self, **_kwargs):
        return {
            "reply": "风格收集完成",
            "options": {"title": "请选择生成数量", "items": ["1", "2", "5"], "max": 1},
            "stage": "image_count",
            "next_stage": "",
            "is_finished": True,
            "fallback_used": False,
        }

    def fake_call_text_model(_self, _provider, _model_name, system_prompt, user_prompt, *, strict_json):
        if "参数提取助手" in system_prompt:
            if "一张" in user_prompt or "1张" in user_prompt:
                return '{"image_count": 1}'
            return '{"image_count": null}'
        if "提示词质检助手" in system_prompt:
            return "READY"
        if strict_json:
            return '{"image_count": null}'
        if "张数：1" in user_prompt:
            return "生成一张西安美食主题图解，聚焦biangbiang面与肉夹馍。"
        return (
            "生成一张西安景点主题图解，聚焦钟楼与路线导览。\n\n"
            "生成一张西安美食主题图解，聚焦biangbiang面与肉夹馍。"
        )

    monkeypatch.setattr(StyleService, "chat", fake_chat)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)

    enter_prompt_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "2"),
        ],
        files=[],
    )
    assert enter_prompt_response.status_code == 200
    enter_prompt_body = enter_prompt_response.json()
    assert enter_prompt_body["draft"]["stage"] == "prompt_revision"
    assert enter_prompt_body["draft"]["image_count"] == 2

    revise_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("text", "一张就行，聚焦西安美食。"),
        ],
        files=[],
    )
    assert revise_response.status_code == 200
    revise_body = revise_response.json()
    assert revise_body["draft"]["stage"] == "prompt_revision"
    assert revise_body["draft"]["image_count"] == 1
    style_prompt = revise_body["draft"]["style_payload"]["style_prompt"]
    assert "生成一张" in style_prompt
    assert style_prompt.count("生成一张") == 1


def test_inspiration_prompt_revision_model_failure_returns_error(client, monkeypatch):
    setup_model_routing(client)
    session = create_session(client, title="修订建议回填校验")

    from backend.app.services.style_service import StyleService

    def fake_chat(_self, **_kwargs):
        return {
            "reply": "风格收集完成",
            "options": {"title": "请选择生成数量", "items": ["1", "2"], "max": 1},
            "stage": "image_count",
            "next_stage": "",
            "is_finished": True,
            "fallback_used": False,
        }

    monkeypatch.setattr(StyleService, "chat", fake_chat)
    def fake_call_text_model(_self, _provider, _model_name, system_prompt, user_prompt, *, strict_json):
        if "READY" in system_prompt:
            return "READY"
        if "分图策划助手" in system_prompt:
            return _fake_allocation_plan_payload(user_prompt)
        if "修订意见：我想生成陕西西安的" in user_prompt:
            return '{"invalid":"json"}'
        return (
            "生成一张统一风格图片，聚焦西安美食细节。\n\n"
            "生成一张统一风格图片，聚焦城市路线与景点导览。"
        )

    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)

    enter_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "2"),
        ],
        files=[],
    )
    assert enter_response.status_code == 200
    assert enter_response.json()["draft"]["stage"] == "prompt_revision"

    revise_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("text", "我想生成陕西西安的，biangbiang面、肉夹馍、冰峰、羊肉泡馍，两张图，一张美食，一张路线。"),
        ],
        files=[],
    )
    assert revise_response.status_code == 503
    revise_body = revise_response.json()
    assert revise_body["code"] == "E-1004"
    assert "模型输出格式异常" in revise_body["message"]


def test_inspiration_use_style_profile_persists_readable_user_message(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="应用风格消息展示会话")
    from backend.app.services.style_service import StyleService

    def fake_call_text_model_with_images(*_args, **_kwargs):
        raise AssertionError("应用风格后引导阶段不应把风格样例图当作内容图送入视觉调用")

    monkeypatch.setattr(
        StyleService,
        "_call_text_model",
        lambda *_args, **_kwargs: "已读取该风格，我先帮你细化需求。请告诉我城市、核心美食/景点，以及计划生成几张图。",
    )
    monkeypatch.setattr(StyleService, "_call_text_model_with_images", fake_call_text_model_with_images)
    image_response = client.post(
        "/api/v1/assets/image",
        data={"session_id": session["id"]},
        files={"file": ("style-sample.png", BytesIO(b"fake-image"), "image/png")},
    )
    assert image_response.status_code == 201
    image_asset = image_response.json()
    style = create_style(
        client,
        session["id"],
        {
            "painting_style": "复古手账插画",
            "color_mood": "暖米黄色",
            "prompt_example": "保持旅行手账拼贴风格。",
            "style_prompt": "保持旅行手账拼贴风格。",
            "sample_image_asset_id": image_asset["id"],
            "extra_keywords": ["复古贴纸", "纸胶带"],
        },
    )

    response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("action", "use_style_profile"),
            ("selected_items", style["id"]),
        ],
        files=[],
    )
    assert response.status_code == 200
    body = response.json()
    user_messages = [msg for msg in body["messages"] if msg["role"] == "user"]
    assert user_messages
    latest_user_message = user_messages[-1]
    assert "已选择风格：" in latest_user_message["content"]
    assert style["name"] in latest_user_message["content"]
    assert "绘画风格：" in latest_user_message["content"]
    assert "色彩情绪：" in latest_user_message["content"]
    image_attachments = [item for item in latest_user_message["attachments"] if item["type"] == "image"]
    assert image_attachments
    assert image_attachments[0]["preview_url"].startswith("http://127.0.0.1:8887/static/images/")
    assert image_attachments[0]["usage_type"] == "style_reference"
    assistant_messages = [msg for msg in body["messages"] if msg["role"] == "assistant"]
    assert assistant_messages
    assert "请告诉我城市、核心美食/景点" in assistant_messages[-1]["content"]
    assert assistant_messages[-1]["options"] is None

