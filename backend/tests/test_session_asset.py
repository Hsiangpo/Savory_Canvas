from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

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
        json={"title": "新会话名", "content_mode": "scenic"},
    )
    assert rename_resp.status_code == 200
    renamed = rename_resp.json()
    assert renamed["id"] == created["id"]
    assert renamed["title"] == "新会话名"
    assert renamed["content_mode"] == "scenic"

    list_resp = client.get("/api/v1/sessions")
    assert list_resp.status_code == 200
    assert list_resp.json()["items"][0]["title"] == "新会话名"
    assert list_resp.json()["items"][0]["content_mode"] == "scenic"

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


def test_text_asset_and_video_transcript(client, monkeypatch):
    from backend.app.services.model_service import ModelService
    import backend.app.workers.transcript_worker as transcript_worker_module

    session = create_session(client)

    def fake_runtime_models(_, provider_payload):
        assert provider_payload["id"]
        return [
            {"id": "gpt-image-1", "name": "gpt-image-1", "capabilities": ["image_generation"]},
            {"id": "gpt-4.1-mini", "name": "gpt-4.1-mini", "capabilities": ["text_generation"]},
            {"id": "whisper-large-v3-turbo", "name": "whisper-large-v3-turbo", "capabilities": ["transcription"]},
        ]

    monkeypatch.setattr(ModelService, "fetch_provider_models", fake_runtime_models)
    provider = client.post(
        "/api/v1/providers",
        json={
            "name": "转写服务商",
            "base_url": "https://example.com",
            "api_key": "secret-key",
            "api_protocol": "responses",
        },
    ).json()
    routing_resp = client.post(
        "/api/v1/config/model-routing",
        json={
            "image_model": {"provider_id": provider["id"], "model_name": "gpt-image-1"},
            "text_model": {"provider_id": provider["id"], "model_name": "gpt-4.1-mini"},
            "transcript_model": {"provider_id": provider["id"], "model_name": "whisper-large-v3-turbo"},
        },
    )
    assert routing_resp.status_code == 200

    class FakeResponse:
        def __init__(self, payload: dict):
            self._payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(_request, timeout=180):
        assert timeout >= 30
        return FakeResponse(
            {
                "text": "苏州园林 桂花糕 评弹",
                "segments": [
                    {"start": 0.0, "end": 1.2, "text": "苏州园林"},
                    {"start": 1.2, "end": 2.4, "text": "桂花糕 评弹"},
                ],
            }
        )

    monkeypatch.setattr(transcript_worker_module, "request", SimpleNamespace(urlopen=fake_urlopen), raising=False)

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

    fake_mp4 = b"\x00\x00\x00ftypmp42" + (b"a" * 2048)
    files = {
        "file": ("苏州园林与点心.mp4", BytesIO(fake_mp4), "video/mp4"),
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
    assert transcript.get("text", "") == "苏州园林 桂花糕 评弹"
    assert transcript.get("segments", []) == [
        {"start": 0.0, "end": 1.2, "text": "苏州园林"},
        {"start": 1.2, "end": 2.4, "text": "桂花糕 评弹"},
    ]


def test_image_asset_upload(client):
    session = create_session(client, title="图片上传会话")

    files = {
        "file": ("style-sample.png", BytesIO(b"fake image bytes"), "image/png"),
    }
    form_data = {"session_id": session["id"]}
    image_response = client.post("/api/v1/assets/image", data=form_data, files=files)
    assert image_response.status_code == 201
    image_asset = image_response.json()
    assert image_asset["session_id"] == session["id"]
    assert image_asset["asset_type"] == "image"
    assert image_asset["status"] == "ready"
    image_name = Path(image_asset["file_path"]).name
    assert image_name.endswith(".png")
    assert not Path(image_asset["file_path"]).is_absolute()


def test_storage_returns_relative_paths(tmp_path):
    from backend.app.infra.storage import Storage

    storage = Storage(tmp_path / "storage")

    video_path = storage.save_video("demo.mp4", b"video")
    image_path = storage.save_image("demo.png", b"image")
    generated_path = storage.save_generated_image("demo-generated.png", b"generated")
    export_path = storage.save_export("demo.pdf", b"pdf-bytes")

    assert video_path == "videos/demo.mp4"
    assert image_path == "images/demo.png"
    assert generated_path == "generated/demo-generated.png"
    assert export_path == "exports/demo.pdf"


def test_not_found_error_codes_for_session_and_asset(client):
    missing_session = client.get("/api/v1/sessions/missing-session")
    assert missing_session.status_code == 404
    assert missing_session.json()["code"] == "E-2001"

    missing_asset = client.get("/api/v1/assets/missing-asset/transcript")
    assert missing_asset.status_code == 404
    assert missing_asset.json()["code"] == "E-2002"




def test_video_asset_requires_transcript_model_routing(client):
    session = create_session(client, title="缺少转写配置")
    files = {
        "file": ("苏州园林与点心.mp4", BytesIO(b"\x00\x00\x00ftypmp42" + (b"a" * 2048)), "video/mp4"),
    }

    response = client.post("/api/v1/assets/video", data={"session_id": session["id"]}, files=files)

    assert response.status_code == 400
    assert response.json()["code"] == "E-1006"



def test_transcript_request_body_uses_json_for_gpt_4o_mini_transcribe(client, tmp_path):
    worker = client.app.state.services.transcript.worker
    media_path = tmp_path / 'sample.mp4'
    media_path.write_bytes(b'\x00\x00\x00ftypmp42' + (b'a' * 2048))

    body, boundary = worker._build_transcription_body(
        file_path=media_path,
        model_name='gpt-4o-mini-transcribe',
    )

    body_text = body.decode('utf-8', errors='ignore')
    assert boundary in body_text
    assert 'name="response_format"' in body_text
    assert '\r\njson\r\n' in body_text
    assert 'timestamp_granularities[]' not in body_text


def test_transcript_request_body_keeps_verbose_json_for_whisper_models(client, tmp_path):
    worker = client.app.state.services.transcript.worker
    media_path = tmp_path / 'sample.mp4'
    media_path.write_bytes(b'\x00\x00\x00ftypmp42' + (b'a' * 2048))

    body, _boundary = worker._build_transcription_body(
        file_path=media_path,
        model_name='whisper-large-v3-turbo',
    )

    body_text = body.decode('utf-8', errors='ignore')
    assert 'name="response_format"' in body_text
    assert '\r\nverbose_json\r\n' in body_text
    assert 'timestamp_granularities[]' in body_text



def test_transcript_request_body_adds_accuracy_prompt_for_gpt_models(client, tmp_path):
    worker = client.app.state.services.transcript.worker
    media_path = tmp_path / 'sample.mp4'
    media_path.write_bytes(b'\x00\x00\x00\x18ftypmp42' + (b'a' * 2048))

    body, _boundary = worker._build_transcription_body(
        file_path=media_path,
        model_name='gpt-4o-mini-transcribe',
    )

    body_text = body.decode('utf-8', errors='ignore')
    assert 'name="prompt"' in body_text
    assert '请使用简体中文准确转写' in body_text
    assert 'name="temperature"' in body_text
    assert '\r\n0\r\n' in body_text


def test_transcript_request_body_adds_accuracy_prompt_for_whisper_models(client, tmp_path):
    worker = client.app.state.services.transcript.worker
    media_path = tmp_path / 'sample.mp4'
    media_path.write_bytes(b'\x00\x00\x00\x18ftypmp42' + (b'a' * 2048))

    body, _boundary = worker._build_transcription_body(
        file_path=media_path,
        model_name='whisper-large-v3-turbo',
    )

    body_text = body.decode('utf-8', errors='ignore')
    assert 'name="prompt"' in body_text
    assert '请使用简体中文准确转写' in body_text
    assert 'name="temperature"' in body_text
