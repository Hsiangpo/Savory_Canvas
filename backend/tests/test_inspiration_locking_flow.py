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
        if "风格归档助手" in system_prompt:
            return (
                '{"style_name":"复古旅行手账风格","painting_style":"手绘水彩+复古手账拼贴",'
                '"color_mood":"暖米黄低饱和","prompt_example":"保持手绘水彩与复古手账拼贴版式，统一暖米黄低饱和色彩。",'
                '"extra_keywords":["手账贴纸","箭头导览","纸胶带"],"style_image_indexes":[]}'
            )
        if "READY" in system_prompt:
            return "READY"
        if strict_json:
            return '{"image_count": null}'
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


def test_inspiration_locked_save_style_uses_llm_summary_and_style_images(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="保存风格抽取校验")

    from backend.app.services.style_service import StyleService

    captured: dict[str, list[str]] = {"summary_image_urls": []}

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
        if "风格归档助手" in system_prompt:
            captured["summary_image_urls"] = list(image_urls)
            assert "可判定图片" in user_prompt
            return (
                '{"style_name":"复古水彩旅行手账图解（泉州）",'
                '"painting_style":"手绘水彩插画 + 复古手账拼贴信息图",'
                '"color_mood":"暖米黄做旧纸张，低饱和棕橙与草绿点缀",'
                '"prompt_example":"保持手绘水彩与复古手账拼贴信息图版式，统一暖米黄低饱和色彩，强调贴纸标注与导览箭头。",'
                '"extra_keywords":["手账贴纸","导览箭头","做旧纸纹"],'
                '"style_image_indexes":[2]}'
            )
        if "资深视觉创意总监" in system_prompt or "更专业、更可执行的中文母提示词" in system_prompt:
            return (
                "生成一张统一风格图片，聚焦美食细节。\n\n"
                "生成一张统一风格图片，聚焦城市路线与景点导览。"
            )
        if "分图策划助手" in system_prompt:
            return _fake_allocation_plan_payload(user_prompt)
        if "图片资产解析助手" in system_prompt:
            return _fake_asset_extract_payload()
        raise AssertionError("未覆盖的多模态调用路径")

    def fake_call_text_model(_self, _provider, _model_name, system_prompt, _user_prompt, *, strict_json):
        if "参数提取助手" in system_prompt:
            return '{"image_count": 2}'
        if "资产提取助手" in system_prompt:
            return _fake_asset_extract_payload()
        if "分图策划助手" in system_prompt:
            return _fake_allocation_plan_payload(_user_prompt)
        if "提示词质检助手" in system_prompt:
            return "READY"
        if strict_json:
            return '{"image_count": null}'
        return (
            "生成一张统一风格图片，聚焦美食细节。\n\n"
            "生成一张统一风格图片，聚焦城市路线与景点导览。"
        )

    monkeypatch.setattr(StyleService, "chat", finish_collecting)
    monkeypatch.setattr(StyleService, "_call_text_model_with_images", fake_call_text_model_with_images)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)

    enter_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("selected_items", "2"),
            ("text", "我想做这种风格的泉州美食景点图解"),
        ],
        files=[
            ("images", ("style-ref-1.png", BytesIO(b"style-image-1"), "image/png")),
            ("images", ("style-ref-2.png", BytesIO(b"style-image-2"), "image/png")),
        ],
    )
    assert enter_response.status_code == 200
    enter_body = enter_response.json()
    user_messages = [message for message in enter_body["messages"] if message["role"] == "user"]
    assert user_messages
    image_attachments = [item for item in user_messages[-1]["attachments"] if item["type"] == "image"]
    assert len(image_attachments) == 2
    expected_selected_asset_id = image_attachments[1]["asset_id"]

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
    save_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("action", "save_style"),
            ("selected_items", "保存风格"),
        ],
        files=[],
    )
    assert save_response.status_code == 200
    assert captured["summary_image_urls"]
    styles = client.get("/api/v1/styles")
    assert styles.status_code == 200
    matched = next((item for item in styles.json()["items"] if item["name"] == "复古水彩旅行手账图解（泉州）"), None)
    assert matched is not None
    payload = matched["style_payload"]
    assert payload["painting_style"] == "手绘水彩插画 + 复古手账拼贴信息图"
    assert payload["color_mood"] == "暖米黄做旧纸张，低饱和棕橙与草绿点缀"
    assert payload["prompt_example"].startswith("保持手绘水彩与复古手账拼贴信息图版式")
    assert payload["sample_image_asset_ids"] == [expected_selected_asset_id]


def test_inspiration_asset_extraction_reads_assets_from_generated_prompt_when_user_says_model_decides(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="模型自主补齐资产")

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
        if "参数提取助手" in system_prompt:
            return '{"image_count": 2}'
        if "READY" in system_prompt:
            return "READY"
        if "资产提取助手" in system_prompt:
            assert "当前母提示词：" in user_prompt
            assert "故宫" in user_prompt
            assert "北京烤鸭" in user_prompt
            return (
                '{"locations":["北京"],"scenes":["故宫"],'
                '"foods":["北京烤鸭"],"keywords":["北京"],"confidence":0.92}'
            )
        if "分图策划助手" in system_prompt:
            return _fake_allocation_plan_payload(user_prompt)
        if strict_json:
            return '{"image_count": 2}'
        return (
            "生成一张北京景点主题的复古旅行手账风格图解海报，围绕故宫与中轴线导览。\n\n"
            "生成一张北京美食主题的复古旅行手账风格图解海报，围绕北京烤鸭与市井烟火。"
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
            ("selected_items", "确认提示词"),
            ("text", "2张，然后你来想景点和美食"),
        ],
        files=[],
    )
    assert confirm_response.status_code == 200
    confirm_body = confirm_response.json()
    assert confirm_body["draft"]["stage"] == "asset_confirming"
    latest_message = confirm_body["messages"][-1]
    candidates = latest_message.get("asset_candidates") or {}
    assert "故宫" in (candidates.get("scenes") or [])
    assert "北京烤鸭" in (candidates.get("foods") or [])


def test_inspiration_requirement_without_image_uses_text_dialog(client, monkeypatch):
    setup_model_routing(client)
    session = create_session(client, title="无图需求引导会话")

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
        if "资深创意策划助手" in system_prompt and not strict_json:
            return "我还没接收到图片，请先补传图片，或直接告诉我城市、美食和张数。"
        return "READY"

    def fail_multimodal(*_args, **_kwargs):
        raise AssertionError("当前消息未上传图片，不应走多模态调用")

    monkeypatch.setattr(StyleService, "chat", finish_collecting)
    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)
    monkeypatch.setattr(StyleService, "_call_text_model_with_images", fail_multimodal)

    response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("text", "如图，就这几种食物"),
        ],
        files=[],
    )
    assert response.status_code == 200
    assert "还没接收到图片" in response.json()["messages"][-1]["content"]
