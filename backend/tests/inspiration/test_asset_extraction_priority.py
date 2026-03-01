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
    return client.post("/api/v1/inspirations/messages", data=payload, files=files or None)


def test_inspiration_asset_candidates_text_priority_over_style_image_semantics(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="资产提取文本优先校验")

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
        user_prompt,
        image_urls,
        *,
        strict_json,
    ):
        assert image_urls
        if "图片资产解析助手" in system_prompt:
            assert strict_json is True
            return (
                '{"locations":["泉州"],"scenes":["开元寺"],'
                '"foods":["面线糊"],"keywords":["泉州古城"],"confidence":0.9}'
            )
        if "分图策划助手" in system_prompt:
            marker = "可用 source_asset_ids："
            source_asset_ids = ["asset-source-1"]
            if marker in user_prompt:
                source_asset_ids = [raw_id.strip() for raw_id in user_prompt.split(marker, 1)[1].splitlines()[0].replace("，", ",").split(",") if raw_id.strip()]
            first_asset_id = source_asset_ids[0]
            second_asset_id = source_asset_ids[1] if len(source_asset_ids) > 1 else first_asset_id
            return (
                '{"items":['
                f'{{"slot_index":1,"focus_title":"景点主图","focus_description":"聚焦兰州中山桥与黄河路线。","locations":["兰州"],"scenes":["中山桥"],"foods":[],"keywords":["黄河沿线"],"source_asset_ids":["{first_asset_id}"]}},'
                f'{{"slot_index":2,"focus_title":"美食主图","focus_description":"聚焦牛肉面与甜醅子。","locations":["兰州"],"scenes":[],"foods":["牛肉面","甜醅子"],"keywords":["兰州美食"],"source_asset_ids":["{second_asset_id}"]}}'
                ']}'
            )
        return (
            "生成一张兰州景点主题图解，聚焦中山桥与黄河线。\n\n"
            "生成一张兰州美食主题图解，聚焦牛肉面与甜醅子。"
        )

    def fake_call_text_model(_self, _provider, _model_name, system_prompt, user_prompt, *, strict_json):
        if "参数提取助手" in system_prompt:
            if "两张" in user_prompt or "2张" in user_prompt:
                return '{"image_count": 2}'
            if "一张" in user_prompt or "1张" in user_prompt:
                return '{"image_count": 1}'
            return '{"image_count": null}'
        if "资产提取助手" in system_prompt:
            if "图片语义" in user_prompt and "泉州" in user_prompt:
                return (
                    '{"locations":["泉州"],"scenes":["开元寺"],'
                    '"foods":["面线糊"],"keywords":["泉州古城"],"confidence":0.9}'
                )
            return (
                '{"locations":["兰州"],"scenes":["中山桥"],'
                '"foods":["牛肉面","甜醅子"],"keywords":["黄河沿线"],"confidence":0.95}'
            )
        if "分图策划助手" in system_prompt:
            marker = "可用 source_asset_ids："
            source_asset_ids = ["asset-source-1"]
            if marker in user_prompt:
                source_asset_ids = [raw_id.strip() for raw_id in user_prompt.split(marker, 1)[1].splitlines()[0].replace("，", ",").split(",") if raw_id.strip()]
            first_asset_id = source_asset_ids[0]
            second_asset_id = source_asset_ids[1] if len(source_asset_ids) > 1 else first_asset_id
            return (
                '{"items":['
                f'{{"slot_index":1,"focus_title":"景点主图","focus_description":"聚焦兰州中山桥与黄河路线。","locations":["兰州"],"scenes":["中山桥"],"foods":[],"keywords":["黄河沿线"],"source_asset_ids":["{first_asset_id}"]}},'
                f'{{"slot_index":2,"focus_title":"美食主图","focus_description":"聚焦牛肉面与甜醅子。","locations":["兰州"],"scenes":[],"foods":["牛肉面","甜醅子"],"keywords":["兰州美食"],"source_asset_ids":["{second_asset_id}"]}}'
                ']}'
            )
        if "READY" in system_prompt:
            return "READY"
        if strict_json:
            return '{"image_count": null}'
        return (
            "生成一张兰州景点主题图解，聚焦中山桥与黄河线。\n\n"
            "生成一张兰州美食主题图解，聚焦牛肉面与甜醅子。"
        )

    monkeypatch.setattr(StyleService, "chat", finish_collecting)
    monkeypatch.setattr(StyleService, "_call_text_model_with_images", fake_call_text_model_with_images)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)

    enter_response = _post_inspiration_message(
        client,
        data=[("session_id", session["id"]), ("selected_items", "2")],
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
            ("text", "我要做兰州美食和景点，两张图，一张景点一张美食。"),
        ],
        files=[],
    )
    assert confirm_response.status_code == 200, confirm_response.text
    body = confirm_response.json()
    assert body["draft"]["stage"] == "asset_confirming"
    candidates = body["messages"][-1]["asset_candidates"]
    assert any("兰州" in item for item in candidates["locations"])
    assert not any("泉州" in item for item in candidates["locations"])
    assert any("牛肉面" in item for item in candidates["foods"])
    assert not any("面线糊" in item for item in candidates["foods"])
    assert not any("泉州" in item for item in candidates["keywords"])


def test_inspiration_asset_candidates_style_reference_images_do_not_fill_content_assets(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="风格参考图不污染资产")

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
        user_prompt,
        image_urls,
        *,
        strict_json,
    ):
        assert image_urls
        if "图片资产解析助手" in system_prompt:
            assert "用户文本上下文" in user_prompt
            assert "这种风格" in user_prompt
            return '{"locations":[],"scenes":[],"foods":[],"keywords":[],"confidence":0.88}'
        if "资深视觉创意总监" in system_prompt:
            return (
                "生成一张泉州景点主题图解，先保留景点位待补充。\n\n"
                "生成一张泉州美食主题图解，先保留美食位待补充。"
            )
        if "更专业、更可执行的中文母提示词" in system_prompt:
            return (
                "生成一张泉州景点主题图解，先保留景点位待补充。\n\n"
                "生成一张泉州美食主题图解，先保留美食位待补充。"
            )
        if "分图策划助手" in system_prompt:
            marker = "可用 source_asset_ids："
            source_asset_ids = ["asset-source-1"]
            if marker in user_prompt:
                source_asset_ids = [
                    raw_id.strip()
                    for raw_id in user_prompt.split(marker, 1)[1].splitlines()[0].replace("，", ",").split(",")
                    if raw_id.strip()
                ]
            first_asset_id = source_asset_ids[0]
            second_asset_id = source_asset_ids[1] if len(source_asset_ids) > 1 else first_asset_id
            return (
                '{"items":['
                f'{{"slot_index":1,"focus_title":"景点主图","focus_description":"先确认景点。","locations":["泉州"],"scenes":[],"foods":[],"keywords":["景点待补充"],"source_asset_ids":["{first_asset_id}"]}},'
                f'{{"slot_index":2,"focus_title":"美食主图","focus_description":"先确认美食。","locations":["泉州"],"scenes":[],"foods":[],"keywords":["美食待补充"],"source_asset_ids":["{second_asset_id}"]}}'
                ']}'
            )
        raise AssertionError("未覆盖的多模态调用路径")

    def fake_call_text_model(_self, _provider, _model_name, system_prompt, user_prompt, *, strict_json):
        if "参数提取助手" in system_prompt:
            if "两张" in user_prompt or "2张" in user_prompt:
                return '{"image_count": 2}'
            return '{"image_count": null}'
        if "资产提取助手" in system_prompt:
            return '{"locations":["福建","泉州"],"scenes":[],"foods":[],"keywords":["泉州攻略"],"confidence":0.93}'
        if "分图策划助手" in system_prompt:
            marker = "可用 source_asset_ids："
            source_asset_ids = ["asset-source-1"]
            if marker in user_prompt:
                source_asset_ids = [
                    raw_id.strip()
                    for raw_id in user_prompt.split(marker, 1)[1].splitlines()[0].replace("，", ",").split(",")
                    if raw_id.strip()
                ]
            first_asset_id = source_asset_ids[0]
            second_asset_id = source_asset_ids[1] if len(source_asset_ids) > 1 else first_asset_id
            return (
                '{"items":['
                f'{{"slot_index":1,"focus_title":"景点主图","focus_description":"先确认景点。","locations":["泉州"],"scenes":[],"foods":[],"keywords":["景点待补充"],"source_asset_ids":["{first_asset_id}"]}},'
                f'{{"slot_index":2,"focus_title":"美食主图","focus_description":"先确认美食。","locations":["泉州"],"scenes":[],"foods":[],"keywords":["美食待补充"],"source_asset_ids":["{second_asset_id}"]}}'
                ']}'
            )
        if "READY" in system_prompt:
            return "READY"
        return (
            "生成一张泉州景点主题图解，先保留景点位待补充。\n\n"
            "生成一张泉州美食主题图解，先保留美食位待补充。"
        )

    monkeypatch.setattr(StyleService, "chat", finish_collecting)
    monkeypatch.setattr(StyleService, "_call_text_model_with_images", fake_call_text_model_with_images)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)

    enter_response = _post_inspiration_message(
        client,
        data=[("session_id", session["id"]), ("text", "这种风格，我想要"), ("selected_items", "2")],
        files=[
            ("images", ("style-ref-1.png", BytesIO(b"style-image-1"), "image/png")),
            ("images", ("style-ref-2.png", BytesIO(b"style-image-2"), "image/png")),
        ],
    )
    assert enter_response.status_code == 200
    assert enter_response.json()["draft"]["stage"] == "prompt_revision"

    confirm_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("action", "confirm_prompt"),
            ("selected_items", "确定使用"),
            ("text", "做福建泉州的美食和景点，两张图。"),
        ],
        files=[],
    )
    assert confirm_response.status_code == 200, confirm_response.text
    body = confirm_response.json()
    candidates = body["messages"][-1]["asset_candidates"]
    assert "福建" in candidates["locations"] or "泉州" in candidates["locations"]
    assert candidates["scenes"] == []
    assert candidates["foods"] == []
