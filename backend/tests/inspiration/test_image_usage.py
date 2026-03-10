from __future__ import annotations

import base64
from io import BytesIO

from conftest import create_session, setup_model_routing

PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6p9x8AAAAASUVORK5CYII="


def test_inspiration_upload_images_respects_image_usages(client, monkeypatch):
    setup_model_routing(client, text_model_name="gpt-4.1")
    session = create_session(client, title="图片用途映射")
    service = client.app.state.services.inspiration
    monkeypatch.setattr(
        service,
        "_run_agent_turn",
        lambda **_: {
            "reply": "已收到图片素材。",
            "stage": "style_collecting",
            "locked": False,
            "trace": [],
        },
    )

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


def test_inspiration_video_only_message_defers_for_transcript(client):
    setup_model_routing(client)
    session = create_session(client, title="视频转写待处理")

    response = client.post(
        "/api/v1/inspirations/messages",
        data={"session_id": session["id"]},
        files=[("videos", ("苏州园林与点心.mp4", BytesIO(b"fake video bytes"), "video/mp4"))],
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["draft"]["stage"] == "transcribing_video"
    assert body["draft"]["progress_label"] == "视频转写中"
    assert "转写" in body["messages"][-1]["content"]



def test_get_conversation_autorun_passes_transcript_text_into_agent(client, monkeypatch):
    from backend.app.core.utils import new_id, now_iso

    session = create_session(client, title="自动转写承接")
    service = client.app.state.services.inspiration
    service._ensure_state(session["id"])

    service.asset_repo.create(
        {
            "id": new_id(),
            "session_id": session["id"],
            "asset_type": "transcript",
            "content": "西安 老菜场 中流巷 书院门",
            "file_path": "videos/demo.mp4",
            "status": "ready",
            "created_at": now_iso(),
        }
    )

    captured: dict[str, object] = {}

    def fake_run_agent_turn(**kwargs):
        captured.update(kwargs)
        return {
            "reply": "我已经拿到转写内容了。",
            "stage": "content_confirmation",
            "locked": False,
            "trace": [],
        }

    monkeypatch.setattr(service, "_run_agent_turn", fake_run_agent_turn)

    response = service.get_conversation(session["id"])

    assert response["messages"][-1]["content"] == "我已经拿到转写内容了。"
    assert captured["action"] == "transcript_ready_auto"
    assert "西安 老菜场 中流巷 书院门" in str(captured["text"])


def test_get_conversation_autorun_persists_asset_candidates_before_count_confirmation(client, monkeypatch):
    from backend.app.core.utils import new_id, now_iso

    session = create_session(client, title="自动转写先落素材")
    service = client.app.state.services.inspiration
    service._ensure_state(session["id"])

    service.asset_repo.create(
        {
            "id": new_id(),
            "session_id": session["id"],
            "asset_type": "transcript",
            "content": "西安 老菜场 中流巷 书院门 德福巷",
            "file_path": "videos/demo.mp4",
            "status": "ready",
            "created_at": now_iso(),
        }
    )

    monkeypatch.setattr(
        service,
        "extract_assets",
        lambda **_: {
            "foods": [],
            "scenes": ["老菜场", "中流巷", "书院门", "德福巷"],
            "keywords": ["西安街区漫游"],
        },
    )
    monkeypatch.setattr(
        service,
        "_run_agent_turn",
        lambda **_: {
            "reply": "素材已经整理好了，接下来确认张数。",
            "stage": "count_confirmation_required",
            "locked": False,
            "trace": [],
            "progress": 45,
            "progress_label": "确认生成张数",
        },
    )

    service.get_conversation(session["id"])

    state = service.inspiration_repo.get_state(session["id"])
    assert state is not None
    assert state["asset_candidates"] == {
        "foods": [],
        "scenes": ["老菜场", "中流巷", "书院门", "德福巷"],
        "keywords": ["西安街区漫游"],
    }


def test_get_conversation_backfills_asset_candidates_for_existing_count_confirmation_session(client, monkeypatch):
    from backend.app.core.utils import new_id, now_iso

    session = create_session(client, title="旧会话素材候选补全")
    service = client.app.state.services.inspiration
    state = service._ensure_state(session["id"])

    transcript_asset_id = new_id()
    service.asset_repo.create(
        {
            "id": transcript_asset_id,
            "session_id": session["id"],
            "asset_type": "transcript",
            "content": "西安 老菜场 中流巷 书院门 德福巷",
            "file_path": "videos/demo.mp4",
            "status": "ready",
            "created_at": now_iso(),
        }
    )
    state.update(
        {
            "stage": "count_confirmation_required",
            "asset_candidates": {},
            "transcript_seen_ids": [transcript_asset_id],
            "updated_at": now_iso(),
        }
    )
    service.inspiration_repo.upsert_state(state)

    monkeypatch.setattr(
        service,
        "extract_assets",
        lambda **_: {
            "foods": [],
            "scenes": ["老菜场", "中流巷", "书院门", "德福巷"],
            "keywords": ["西安街区漫游"],
        },
    )
    monkeypatch.setattr(
        service,
        "_run_agent_turn",
        lambda **_: {
            "reply": "继续确认张数。",
            "stage": "count_confirmation_required",
            "locked": False,
            "trace": [],
            "progress": 45,
            "progress_label": "确认生成张数",
        },
    )

    service.get_conversation(session["id"])

    refreshed = service.inspiration_repo.get_state(session["id"])
    assert refreshed is not None
    assert refreshed["asset_candidates"] == {
        "foods": [],
        "scenes": ["老菜场", "中流巷", "书院门", "德福巷"],
        "keywords": ["西安街区漫游"],
    }
