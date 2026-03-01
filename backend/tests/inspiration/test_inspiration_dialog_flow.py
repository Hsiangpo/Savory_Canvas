from __future__ import annotations

import json
from io import BytesIO
from typing import Any

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


def _fake_allocation_plan_payload(user_prompt: str = "") -> str:
    marker = "可用 source_asset_ids："
    source_asset_ids: list[str] = []
    if marker in user_prompt:
        source_asset_ids = [
            raw_id.strip()
            for raw_id in user_prompt.split(marker, 1)[1].splitlines()[0].replace("，", ",").split(",")
            if raw_id.strip()
        ]
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


def test_inspiration_collecting_stage_uses_multimodal_when_user_uploads_image(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="多模态对话校验")

    from backend.app.services.style_service import StyleService

    captured: dict[str, Any] = {"image_urls": []}

    def fake_call_text_model_with_images(
        _self,
        _provider,
        _model_name,
        _system_prompt,
        _user_prompt,
        image_urls,
        *,
        strict_json,
    ):
        if "分图策划助手" in _system_prompt:
            assert strict_json is True
            return _fake_allocation_plan_payload(_user_prompt)
        assert strict_json is False
        captured["image_urls"] = list(image_urls)
        return json.dumps(
            {
                "reply": "我看到了你上传的参考图，我们先对齐本次的地点和核心内容。",
                "options": {"title": "请选择绘画风格", "items": ["手绘水彩", "复古手账"], "max": 2},
            },
            ensure_ascii=False,
        )

    def fail_call_text_model(*_args, **_kwargs):
        raise AssertionError("collecting 阶段上传图片后应走多模态调用，不应回退纯文本调用")

    monkeypatch.setattr(StyleService, "_call_text_model_with_images", fake_call_text_model_with_images)
    monkeypatch.setattr(StyleService, "_call_text_model", fail_call_text_model)

    response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("text", "我想做西安美食与景点图解"),
        ],
        files=[("images", ("xian-reference.png", BytesIO(b"image"), "image/png"))],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["messages"][-1]["role"] == "assistant"
    assert "我看到了你上传的参考图" in body["messages"][-1]["content"]
    assert captured["image_urls"]
    assert captured["image_urls"][0].startswith("http://127.0.0.1:8887/static/images/")


def test_inspiration_allocation_plan_uses_recent_four_images(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="分图读图取最近四张")

    from backend.app.services.style_service import StyleService

    captured: dict[str, Any] = {"allocation_image_urls": []}

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
        if "分图策划助手" in system_prompt:
            captured["allocation_image_urls"] = list(image_urls)
            return _fake_allocation_plan_payload(user_prompt)
        if "图片资产解析助手" in system_prompt:
            return '{"locations":["西安"],"scenes":["钟楼"],"foods":["肉夹馍"],"keywords":["西安攻略"],"confidence":0.9}'
        if "资深视觉创意总监" in system_prompt or "更专业、更可执行的中文母提示词" in system_prompt:
            return (
                "生成一张西安景点主题图解，聚焦钟楼与路线导览。\n\n"
                "生成一张西安美食主题图解，聚焦肉夹馍与冰峰。"
            )
        return json.dumps(
            {
                "reply": "风格已确认，请补充你要突出的地点与美食。",
                "options": {"title": "请选择绘画风格", "items": ["手绘水彩"], "max": 1},
            },
            ensure_ascii=False,
        )

    def fake_call_text_model(_self, _provider, _model_name, system_prompt, user_prompt, *, strict_json):
        if "参数提取助手" in system_prompt:
            if "两张" in user_prompt or "2张" in user_prompt:
                return '{"image_count": 2}'
            return '{"image_count": null}'
        if "资产提取助手" in system_prompt:
            return '{"locations":["西安"],"scenes":["钟楼"],"foods":["肉夹馍"],"keywords":["西安攻略"],"confidence":0.95}'
        if "提示词质检助手" in system_prompt:
            return "READY"
        if "分图策划助手" in system_prompt:
            return _fake_allocation_plan_payload(user_prompt)
        return (
            "生成一张西安景点主题图解，聚焦钟楼与路线导览。\n\n"
            "生成一张西安美食主题图解，聚焦肉夹馍与冰峰。"
        )

    monkeypatch.setattr(StyleService, "chat", finish_collecting)
    monkeypatch.setattr(StyleService, "_call_text_model_with_images", fake_call_text_model_with_images)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)

    files = [
        ("images", ("img-1.png", BytesIO(b"image-1"), "image/png")),
        ("images", ("img-2.png", BytesIO(b"image-2"), "image/png")),
        ("images", ("img-3.png", BytesIO(b"image-3"), "image/png")),
        ("images", ("img-4.png", BytesIO(b"image-4"), "image/png")),
        ("images", ("img-5.png", BytesIO(b"image-5"), "image/png")),
        ("images", ("img-6.png", BytesIO(b"image-6"), "image/png")),
    ]
    enter_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("text", "这种风格我想要，两张图。"),
            ("selected_items", "2"),
        ],
        files=files,
    )
    assert enter_response.status_code == 200
    enter_body = enter_response.json()
    user_messages = [message for message in enter_body["messages"] if message["role"] == "user"]
    assert user_messages
    image_urls = [
        attachment.get("preview_url")
        for attachment in user_messages[-1]["attachments"]
        if attachment.get("type") == "image"
    ]
    assert len(image_urls) == 6

    confirm_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("action", "confirm_prompt"),
            ("selected_items", "确认提示词"),
            ("text", "做西安景点和美食，两张图。"),
        ],
        files=[],
    )
    assert confirm_response.status_code == 200
    assert captured["allocation_image_urls"] == image_urls[-4:]


def test_inspiration_finished_style_without_count_enters_prompt_dialog(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="未给张数时进入引导对话")

    from backend.app.services.style_service import StyleService

    def finish_collecting_without_count(_self, **_kwargs):
        return {
            "reply": "风格收集已完成",
            "options": {"title": "请选择生成数量", "items": ["生成1张"], "max": 1},
            "stage": "image_count",
            "next_stage": "",
            "is_finished": True,
            "fallback_used": False,
        }

    def fake_call_text_model(_self, _provider, _model_name, system_prompt, _user_prompt, *, strict_json):
        if "创意策划助手" in system_prompt and not strict_json:
            return "好的，我先确认需求：想做哪个城市？重点是景点还是美食？本次计划生成几张图？"
        return (
            "生成一张示例风格图，突出复古手账材质。\n\n"
            "生成一张示例风格图，突出拼贴装饰。"
        )

    monkeypatch.setattr(StyleService, "chat", finish_collecting_without_count)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)

    response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "复古牛皮纸+手绘贴纸边框"),
        ],
        files=[],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["stage"] == "prompt_revision"
    assert body["messages"][-1]["options"] is None
    assert "计划生成几张图" in body["messages"][-1]["content"]


def test_inspiration_requirement_dialog_reads_uploaded_image_before_questioning(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="需求引导读图校验")

    from backend.app.services.style_service import StyleService

    captured: dict[str, Any] = {"image_url_calls": []}

    def finish_collecting_without_count(_self, **_kwargs):
        return {
            "reply": "风格收集已完成",
            "options": {"title": "请选择生成数量", "items": ["生成1张"], "max": 1},
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
        assert "资深创意策划助手" in system_prompt
        assert strict_json is False
        captured["image_url_calls"].append(list(image_urls))
        return "我先从你给的图里识别到牛肉面、烤肉和杏皮茶。请确认是否就围绕这几种美食来做。"

    def fail_call_text_model(_self, _provider, _model_name, system_prompt, _user_prompt, *, strict_json):
        if "资深创意策划助手" in system_prompt and not strict_json:
            raise AssertionError("有图片时需求引导阶段应走多模态调用")
        return "READY"

    monkeypatch.setattr(StyleService, "chat", finish_collecting_without_count)
    monkeypatch.setattr(StyleService, "_call_text_model_with_images", fake_call_text_model_with_images)
    monkeypatch.setattr(StyleService, "_call_text_model", fail_call_text_model)

    response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("text", "如图所示，就这几种食物就行"),
        ],
        files=[("images", ("food-reference.png", BytesIO(b"food-image"), "image/png"))],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["stage"] == "prompt_revision"
    assert "我先从你给的图里识别到" in body["messages"][-1]["content"]
    assert captured["image_url_calls"]
    assert captured["image_url_calls"][0]
    assert captured["image_url_calls"][0][0].startswith("http://127.0.0.1:8887/static/images/")

    followup_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("text", "先围绕美食，再决定景点。"),
        ],
        files=[],
    )
    assert followup_response.status_code == 200
    assert len(captured["image_url_calls"]) >= 2
    assert captured["image_url_calls"][1]
    assert captured["image_url_calls"][1][0].startswith("http://127.0.0.1:8887/static/images/")
