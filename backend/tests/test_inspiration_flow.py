from __future__ import annotations

from io import BytesIO

from conftest import create_session, setup_model_routing, wait_until


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

    monkeypatch.setattr(StyleService, "chat", finish_collecting)
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

    first_response = _post_inspiration_message(
        client,
        data=[
            ("session_id", session["id"]),
            ("text", "我想做法式晚宴主题"),
            ("selected_items", "油画厚涂"),
            ("selected_items", "电影写实"),
            ("image_usages", "style_reference"),
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
    assert image_attachments[0]["usage_type"] == "style_reference"

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
    assert second_response.status_code == 200
    second_body = second_response.json()
    assert second_body["draft"]["stage"] == "prompt_revision"
    latest_message = second_body["messages"][-1]["content"]
    assert "已生成风格提示词" in latest_message
    assert "风格参数：{" not in latest_message


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
