from __future__ import annotations

from io import BytesIO
from pathlib import Path

from conftest import create_session, wait_until


def test_session_create_list_and_detail(client):
    created = create_session(client, title="我的会话")
    assert created["title"] == "我的会话"

    listed = client.get("/api/v1/sessions")
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1

    detail = client.get(f"/api/v1/sessions/{created['id']}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["session"]["id"] == created["id"]
    assert payload["assets"] == []
    assert payload["jobs"] == []
    assert payload["exports"] == []


def test_session_rename_and_delete_flow(client):
    created = create_session(client, title="旧会话名")

    rename_resp = client.patch(
        f"/api/v1/sessions/{created['id']}",
        json={"title": "新会话名"},
    )
    assert rename_resp.status_code == 200
    renamed = rename_resp.json()
    assert renamed["id"] == created["id"]
    assert renamed["title"] == "新会话名"
    assert renamed["content_mode"] == created["content_mode"]

    list_resp = client.get("/api/v1/sessions")
    assert list_resp.status_code == 200
    assert list_resp.json()["items"][0]["title"] == "新会话名"

    delete_resp = client.delete(f"/api/v1/sessions/{created['id']}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] is True

    detail_after_delete = client.get(f"/api/v1/sessions/{created['id']}")
    assert detail_after_delete.status_code == 404
    assert detail_after_delete.json()["code"] == "E-2001"


def test_session_rename_and_delete_not_found(client):
    rename_resp = client.patch(
        "/api/v1/sessions/missing-session",
        json={"title": "不存在会话"},
    )
    assert rename_resp.status_code == 404
    assert rename_resp.json()["code"] == "E-2001"

    delete_resp = client.delete("/api/v1/sessions/missing-session")
    assert delete_resp.status_code == 404
    assert delete_resp.json()["code"] == "E-2001"


def test_text_asset_and_video_transcript(client):
    session = create_session(client)

    text_resp = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "text",
            "content": "今天想做一道番茄炖牛腩",
        },
    )
    assert text_resp.status_code == 201
    text_asset = text_resp.json()
    assert text_asset["status"] == "ready"

    files = {
        "file": ("demo.mp4", BytesIO(b"fake video bytes"), "video/mp4"),
    }
    form_data = {"session_id": session["id"]}
    video_resp = client.post("/api/v1/assets/video", data=form_data, files=files)
    assert video_resp.status_code == 201
    video_asset = video_resp.json()
    video_name = Path(video_asset["file_path"]).name
    assert video_name.endswith(".mp4")
    assert not video_name.endswith(".mp4.mp4")

    def _poll_transcript():
        response = client.get(f"/api/v1/assets/{video_asset['id']}/transcript")
        if response.status_code != 200:
            return None
        body = response.json()
        if body["status"] in {"ready", "failed"}:
            return body
        return None

    transcript = wait_until(_poll_transcript, timeout=5.0, interval=0.1)
    assert transcript is not None
    assert transcript["status"] == "ready"
    assert isinstance(transcript.get("text", ""), str)


def test_not_found_error_codes_for_session_and_asset(client):
    missing_session = client.get("/api/v1/sessions/missing-session")
    assert missing_session.status_code == 404
    assert missing_session.json()["code"] == "E-2001"

    missing_asset = client.get("/api/v1/assets/missing-asset/transcript")
    assert missing_asset.status_code == 404
    assert missing_asset.json()["code"] == "E-2002"

