from __future__ import annotations

import base64
from io import BytesIO

from conftest import create_session, setup_model_routing

PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6p9x8AAAAASUVORK5CYII="


def test_inspiration_upload_images_respects_image_usages(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="图片用途映射")
    service = client.app.state.services.inspiration
    service.agent_mode = "legacy"

    from backend.app.services.style_service import StyleService

    def fake_chat(_self, **_kwargs):
        return {
            "reply": "已收到图片素材。",
            "options": None,
            "stage": "painting_style",
            "next_stage": "painting_style",
            "is_finished": False,
            "fallback_used": False,
        }

    monkeypatch.setattr(StyleService, "chat", fake_chat)

    response = client.post(
        "/api/v1/inspirations/messages",
        data={
            "session_id": session["id"],
            "text": "第一张是风格参考，第二张是内容素材。",
            "image_usages": ["style_reference", "content_asset"],
        },
        files=[
            ("images", ("style-reference.png", BytesIO(base64.b64decode(PNG_BASE64)), "image/png")),
            ("images", ("content-asset.png", BytesIO(base64.b64decode(PNG_BASE64)), "image/png")),
        ],
    )

    assert response.status_code == 200, response.text
    body = response.json()
    user_messages = [message for message in body["messages"] if message["role"] == "user"]
    assert user_messages
    image_attachments = [item for item in user_messages[-1]["attachments"] if item["type"] == "image"]
    assert [item["usage_type"] for item in image_attachments] == ["style_reference", "content_asset"]
