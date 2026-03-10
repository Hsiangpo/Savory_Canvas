from __future__ import annotations

import json
from pathlib import Path
from urllib import error as url_error

from conftest import (
    create_generation_job,
    create_session,
    create_style,
    setup_model_routing,
    wait_for_export_end,
    wait_for_job_end,
)


def test_provider_model_routing_and_models(client):
    provider = setup_model_routing(client)

    list_resp = client.get("/api/v1/providers")
    assert list_resp.status_code == 200
    assert len(list_resp.json()["items"]) == 1

    model_resp = client.get(f"/api/v1/models?provider_id={provider['id']}")
    assert model_resp.status_code == 200
    model_data = model_resp.json()
    assert model_data["provider_id"] == provider["id"]
    assert len(model_data["items"]) >= 2

    route_get = client.get("/api/v1/config/model-routing")
    assert route_get.status_code == 200
    route_data = route_get.json()
    assert route_data["image_model"]["provider_id"] == provider["id"]
    assert route_data["text_model"]["provider_id"] == provider["id"]

    patch_resp = client.patch(
        f"/api/v1/providers/{provider['id']}",
        json={"enabled": False},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["enabled"] is False

    del_resp = client.delete(f"/api/v1/providers/{provider['id']}")
    assert del_resp.status_code == 200




def test_model_routing_supports_transcript_model(client, monkeypatch):
    from backend.app.services.model_service import ModelService

    provider = client.post(
        "/api/v1/providers",
        json={
            "name": "转写模型提供商",
            "base_url": "https://example.com",
            "api_key": "transcript-key",
            "api_protocol": "responses",
        },
    ).json()

    def fake_runtime_models(_, provider_payload):
        assert provider_payload["id"] == provider["id"]
        return [
            {"id": "gpt-image-1", "name": "gpt-image-1", "capabilities": ["image_generation"]},
            {"id": "gpt-4.1-mini", "name": "gpt-4.1-mini", "capabilities": ["text_generation"]},
            {"id": "whisper-large-v3-turbo", "name": "whisper-large-v3-turbo", "capabilities": ["transcription"]},
        ]

    monkeypatch.setattr(ModelService, "fetch_provider_models", fake_runtime_models)
    response = client.post(
        "/api/v1/config/model-routing",
        json={
            "image_model": {"provider_id": provider["id"], "model_name": "gpt-image-1"},
            "text_model": {"provider_id": provider["id"], "model_name": "gpt-4.1-mini"},
            "transcript_model": {"provider_id": provider["id"], "model_name": "whisper-large-v3-turbo"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["transcript_model"]["provider_id"] == provider["id"]
    assert body["transcript_model"]["model_name"] == "whisper-large-v3-turbo"


def test_infer_capabilities_supports_transcription_variants():
    from backend.app.services.model_service import ModelService

    service = ModelService(config_repo=None, provider_repo=None)  # type: ignore[arg-type]
    for model_name in ["whisper-large-v3-turbo", "gpt-4o-transcribe", "distil-whisper-large-v3-en"]:
        capabilities = service._infer_capabilities(model_name)
        assert "transcription" in capabilities

def test_provider_api_key_is_encrypted_at_rest_and_masked_to_last_four(client):
    create_response = client.post(
        "/api/v1/providers",
        json={
            "name": "加密提供商",
            "base_url": "https://example.com",
            "api_key": "secret-provider-key",
            "api_protocol": "responses",
        },
    )
    assert create_response.status_code == 201
    provider = create_response.json()
    assert provider["api_key_masked"].endswith("y")
    assert provider["api_key_masked"].startswith("*")

    row = client.app.state.services.provider.provider_repo.get(provider["id"])
    assert row is not None
    assert row["api_key"] == "secret-provider-key"

    raw_row = client.app.state.services.provider.provider_repo.db.fetch_one(
        "SELECT api_key, api_key_masked FROM provider_config WHERE id = ?",
        (provider["id"],),
    )
    assert raw_row is not None
    assert raw_row["api_key"] != "secret-provider-key"
    assert raw_row["api_key_masked"].endswith("key")


def test_model_list_uses_runtime_provider_models(client, monkeypatch):
    from backend.app.services.model_service import ModelService

    provider = client.post(
        "/api/v1/providers",
        json={
            "name": "实时模型提供商",
            "base_url": "https://example.com",
            "api_key": "runtime-model-key",
            "api_protocol": "responses",
        },
    ).json()

    def fake_runtime_models(_, provider_payload):
        assert provider_payload["id"] == provider["id"]
        return [
            {"id": "runtime-image-1", "name": "runtime-image-1", "capabilities": ["image_generation"]},
            {"id": "runtime-text-1", "name": "runtime-text-1", "capabilities": ["text_generation"]},
        ]

    monkeypatch.setattr(ModelService, "fetch_provider_models", fake_runtime_models)
    response = client.get(f"/api/v1/models?provider_id={provider['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["provider_id"] == provider["id"]
    assert body["items"][0]["name"] == "runtime-image-1"
    assert body["items"][1]["name"] == "runtime-text-1"


def test_get_model_routing_returns_null_when_not_configured(client):
    response = client.get("/api/v1/config/model-routing")
    assert response.status_code == 200
    assert response.json() is None


def test_get_model_routing_returns_null_after_provider_deleted(client):
    provider = setup_model_routing(client)
    delete_response = client.delete(f"/api/v1/providers/{provider['id']}")
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True

    route_response = client.get("/api/v1/config/model-routing")
    assert route_response.status_code == 200
    assert route_response.json() is None


def test_model_routing_requires_capability_match(client):
    provider = client.post(
        "/api/v1/providers",
        json={
            "name": "能力校验提供商",
            "base_url": "https://example.com",
            "api_key": "capability-key",
            "api_protocol": "responses",
        },
    ).json()

    wrong_image = client.post(
        "/api/v1/config/model-routing",
        json={
            "image_model": {
                "provider_id": provider["id"],
                "model_name": "gpt-4.1-mini",
            },
            "text_model": {
                "provider_id": provider["id"],
                "model_name": "gpt-4.1",
            },
            "transcript_model": {
                "provider_id": provider["id"],
                "model_name": "whisper-large-v3-turbo",
            },
        },
    )
    assert wrong_image.status_code == 400
    assert wrong_image.json()["code"] == "E-1006"
    assert "image_generation" in wrong_image.json()["message"]

    wrong_text = client.post(
        "/api/v1/config/model-routing",
        json={
            "image_model": {
                "provider_id": provider["id"],
                "model_name": "gpt-image-1",
            },
            "text_model": {
                "provider_id": provider["id"],
                "model_name": "gpt-image-1",
            },
            "transcript_model": {
                "provider_id": provider["id"],
                "model_name": "whisper-large-v3-turbo",
            },
        },
    )
    assert wrong_text.status_code == 400
    assert wrong_text.json()["code"] == "E-1006"
    assert "text_generation" in wrong_text.json()["message"]


def test_create_provider_rejects_invalid_api_protocol(client):
    response = client.post(
        "/api/v1/providers",
        json={
            "name": "非法协议提供商",
            "base_url": "https://example.com",
            "api_key": "any-key",
            "api_protocol": "openai",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "E-1099"


def test_infer_capabilities_supports_nano_banana_variants():
    from backend.app.services.model_service import ModelService

    service = ModelService(config_repo=None, provider_repo=None)  # type: ignore[arg-type]
    variants = [
        "nano-banana-pro",
        "nano-banana-hd",
        "nano-banana-2",
        "nano-banana-2-ultra",
    ]

    for model_name in variants:
        capabilities = service._infer_capabilities(model_name)
        assert "image_generation" in capabilities


def test_infer_capabilities_supports_china_image_model_variants():
    from backend.app.services.model_service import ModelService

    service = ModelService(config_repo=None, provider_repo=None)  # type: ignore[arg-type]
    variants = [
        "qwen-image-plus-2026-01-09",
        "qwen-image-edit-max-2026-01-16",
        "doubao-seedream-5-0-260128",
        "doubao-seededit-3-0-i2i-250628",
    ]
    for model_name in variants:
        capabilities = service._infer_capabilities(model_name)
        assert "image_generation" in capabilities


def test_infer_capabilities_keeps_video_generation_out_of_image_generation():
    from backend.app.services.model_service import ModelService

    service = ModelService(config_repo=None, provider_repo=None)  # type: ignore[arg-type]
    capabilities = service._infer_capabilities("wanx2.1-t2v-plus")
    assert "image_generation" not in capabilities
    assert "text_generation" in capabilities


def test_infer_capabilities_keeps_non_image_model_text_generation():
    from backend.app.services.model_service import ModelService

    service = ModelService(config_repo=None, provider_repo=None)  # type: ignore[arg-type]
    gpt_41_caps = service._infer_capabilities("gpt-4.1-mini")
    assert "text_generation" in gpt_41_caps
    assert "image_generation" not in gpt_41_caps

    gpt_4o_caps = service._infer_capabilities("gpt-4o-mini")
    assert gpt_4o_caps == ["text_generation", "vision"]


def test_fetch_provider_models_merges_upstream_and_local_capabilities(monkeypatch):
    from backend.app.services.model_service import ModelService

    service = ModelService(config_repo=None, provider_repo=None)  # type: ignore[arg-type]
    provider = {
        "base_url": "https://example.com",
        "api_key": "provider-secret",
    }

    class FakeResponse:
        def __init__(self, payload_text: str):
            self._payload_text = payload_text

        def read(self):
            return self._payload_text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(_request, timeout):
        assert timeout == 10
        payload = {
            "data": [
                {
                    "id": "nano-banana-pro",
                    "capabilities": ["text_generation"],
                }
            ]
        }
        return FakeResponse(json.dumps(payload))

    monkeypatch.setattr("backend.app.services.model_service.request.urlopen", fake_urlopen)
    models = service.fetch_provider_models(provider)
    assert models[0]["name"] == "nano-banana-pro"
    assert "image_generation" in models[0]["capabilities"]
    assert "text_generation" in models[0]["capabilities"]


def test_fetch_provider_models_merges_qwen_image_as_image_generation_even_when_upstream_is_text(monkeypatch):
    from backend.app.services.model_service import ModelService

    service = ModelService(config_repo=None, provider_repo=None)  # type: ignore[arg-type]
    provider = {
        "base_url": "https://example.com",
        "api_key": "provider-secret",
    }

    class FakeResponse:
        def __init__(self, payload_text: str):
            self._payload_text = payload_text

        def read(self):
            return self._payload_text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(_request, timeout):
        assert timeout == 10
        payload = {"data": [{"id": "qwen-image-plus-2026-01-09", "capabilities": ["text_generation"]}]}
        return FakeResponse(json.dumps(payload))

    monkeypatch.setattr("backend.app.services.model_service.request.urlopen", fake_urlopen)
    models = service.fetch_provider_models(provider)
    assert models[0]["name"] == "qwen-image-plus-2026-01-09"
    assert "image_generation" in models[0]["capabilities"]
    assert "text_generation" in models[0]["capabilities"]


def test_fetch_provider_models_infers_nano_banana_as_image_generation_when_upstream_missing(monkeypatch):
    from backend.app.services.model_service import ModelService

    service = ModelService(config_repo=None, provider_repo=None)  # type: ignore[arg-type]
    provider = {
        "base_url": "https://example.com",
        "api_key": "provider-secret",
    }

    class FakeResponse:
        def __init__(self, payload_text: str):
            self._payload_text = payload_text

        def read(self):
            return self._payload_text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(_request, timeout):
        assert timeout == 10
        payload = {"data": [{"id": "nano-banana-pro"}]}
        return FakeResponse(json.dumps(payload))

    monkeypatch.setattr("backend.app.services.model_service.request.urlopen", fake_urlopen)
    models = service.fetch_provider_models(provider)
    assert models[0]["name"] == "nano-banana-pro"
    assert "image_generation" in models[0]["capabilities"]


def test_fetch_provider_models_keeps_plain_text_model_out_of_image_generation(monkeypatch):
    from backend.app.services.model_service import ModelService

    service = ModelService(config_repo=None, provider_repo=None)  # type: ignore[arg-type]
    provider = {
        "base_url": "https://example.com",
        "api_key": "provider-secret",
    }

    class FakeResponse:
        def __init__(self, payload_text: str):
            self._payload_text = payload_text

        def read(self):
            return self._payload_text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(_request, timeout):
        assert timeout == 10
        payload = {"data": [{"id": "gpt-3.5-turbo", "capabilities": ["text_generation"]}]}
        return FakeResponse(json.dumps(payload))

    monkeypatch.setattr("backend.app.services.model_service.request.urlopen", fake_urlopen)
    models = service.fetch_provider_models(provider)
    assert models[0]["name"] == "gpt-3.5-turbo"
    assert "text_generation" in models[0]["capabilities"]
    assert "image_generation" not in models[0]["capabilities"]


def test_fetch_provider_models_uses_cache_when_upstream_temporarily_unavailable(monkeypatch):
    from backend.app.services.model_service import ModelService

    service = ModelService(config_repo=None, provider_repo=None)  # type: ignore[arg-type]
    provider = {
        "id": "provider-cache-demo",
        "base_url": "https://example.com",
        "api_key": "provider-secret",
    }
    call_count = 0

    class FakeResponse:
        def __init__(self, payload_text: str):
            self._payload_text = payload_text

        def read(self):
            return self._payload_text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(_request, timeout):
        nonlocal call_count
        assert timeout == 10
        call_count += 1
        if call_count == 1:
            payload = {"data": [{"id": "gpt-5.1"}]}
            return FakeResponse(json.dumps(payload))
        raise url_error.URLError("Temporary network error")

    monkeypatch.setattr("backend.app.services.model_service.request.urlopen", fake_urlopen)
    first_models = service.fetch_provider_models(provider)
    assert first_models[0]["name"] == "gpt-5.1"
    second_models = service.fetch_provider_models(provider)
    assert second_models[0]["name"] == "gpt-5.1"


def test_fetch_provider_models_fallbacks_to_v1_models_when_root_models_returns_html(monkeypatch):
    from backend.app.services.model_service import ModelService

    service = ModelService(config_repo=None, provider_repo=None)  # type: ignore[arg-type]
    provider = {
        "base_url": "https://example.com",
        "api_key": "provider-secret",
    }
    requested_urls: list[str] = []

    class FakeResponse:
        def __init__(self, payload_text: str):
            self._payload_text = payload_text

        def read(self):
            return self._payload_text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(upstream_request, timeout):
        assert timeout == 10
        requested_urls.append(upstream_request.full_url)
        if upstream_request.full_url.endswith("/v1/models"):
            payload = {"data": [{"id": "gpt-4.1-mini"}]}
            return FakeResponse(json.dumps(payload))
        return FakeResponse("<!doctype html><html><body>not-json</body></html>")

    monkeypatch.setattr("backend.app.services.model_service.request.urlopen", fake_urlopen)
    models = service.fetch_provider_models(provider)
    assert [item["name"] for item in models] == ["gpt-4.1-mini"]
    assert requested_urls[:2] == ["https://example.com/models", "https://example.com/v1/models"]


def test_export_task_flow(client):
    setup_model_routing(client)
    session = create_session(client)

    asset_resp = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "text",
            "content": "法式烤鸡与烛光晚餐场景",
        },
    )
    assert asset_resp.status_code == 201

    style = create_style(client, session["id"], {"painting_style": ["法式复古"]})
    job = create_generation_job(client, session["id"], style["id"], image_count=1)
    final_job = wait_for_job_end(client, job["id"])
    assert final_job["status"] in {"success", "partial_success"}

    export_resp = client.post(
        "/api/v1/exports",
        json={
            "session_id": session["id"],
            "job_id": job["id"],
            "export_format": "pdf",
        },
    )
    assert export_resp.status_code == 202
    export_task = export_resp.json()

    final_export = wait_for_export_end(client, export_task["id"])
    assert final_export["status"] == "success"
    assert isinstance(final_export.get("file_url", ""), str)
    assert final_export["file_url"].startswith("http://127.0.0.1:8887/static/exports/")
    relative_path = final_export["file_url"].split("/static/", 1)[1]
    export_file = Path(client.app.state.services.export.storage.base_dir) / Path(*relative_path.split("/"))
    assert export_file.is_file()
    assert export_file.read_bytes().startswith(b"%PDF")


def test_export_task_flow_long_image_outputs_png(client):
    setup_model_routing(client)
    session = create_session(client)
    asset_resp = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "text",
            "content": "用一页长图导出西安美食与景点推荐",
        },
    )
    assert asset_resp.status_code == 201

    style = create_style(client, session["id"], {"painting_style": ["旅行手账"]})
    job = create_generation_job(client, session["id"], style["id"], image_count=1)
    final_job = wait_for_job_end(client, job["id"])
    assert final_job["status"] in {"success", "partial_success"}

    export_resp = client.post(
        "/api/v1/exports",
        json={
            "session_id": session["id"],
            "job_id": job["id"],
            "export_format": "long_image",
        },
    )
    assert export_resp.status_code == 202
    export_task = export_resp.json()

    final_export = wait_for_export_end(client, export_task["id"])
    assert final_export["status"] == "success"
    assert isinstance(final_export.get("file_url", ""), str)
    assert final_export["file_url"].startswith("http://127.0.0.1:8887/static/exports/")
    assert final_export["file_url"].endswith(".png")
    relative_path = final_export["file_url"].split("/static/", 1)[1]
    export_file = Path(client.app.state.services.export.storage.base_dir) / Path(*relative_path.split("/"))
    assert export_file.is_file()
    assert export_file.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_export_service_public_url_helper_does_not_mutate_input_task(client):
    export_service = client.app.state.services.export
    original_task = {
        "id": "task-1",
        "file_url": "exports/task-1.pdf",
    }

    resolved_task = export_service._with_public_file_url(original_task)

    assert resolved_task["file_url"].startswith("http://127.0.0.1:8887/static/exports/")
    assert original_task["file_url"] == "exports/task-1.pdf"


def test_export_worker_prefers_configured_font_paths(client, monkeypatch):
    worker = client.app.state.services.export.worker
    worker.font_paths = ["D:/fonts/custom-font.ttf"]
    attempted_paths: list[str] = []

    class FakeImageFont:
        @staticmethod
        def truetype(path, size):
            attempted_paths.append(path)
            raise OSError("font not found")

        @staticmethod
        def load_default():
            return "default-font"

    loaded_font = worker._load_font(size=24, bold=False, fallback=FakeImageFont)

    assert loaded_font == "default-font"
    assert attempted_paths[0] == "D:/fonts/custom-font.ttf"


def test_generation_requires_model_routing(client):
    session = create_session(client)
    asset_response = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "text",
            "content": "清爽沙拉与阳光厨房场景",
        },
    )
    assert asset_response.status_code == 201

    style = create_style(client, session["id"], {"painting_style": ["写实"]})
    job = create_generation_job(client, session["id"], style["id"], image_count=1)
    final_job = wait_for_job_end(client, job["id"])
    assert final_job["status"] == "failed"
    assert final_job["error_code"] == "E-1006"
    assert final_job["error_message"] == "请先完成模型设置"


def test_not_found_error_codes_for_provider_job_export(client):
    missing_provider = client.patch(
        "/api/v1/providers/missing-provider",
        json={"enabled": False},
    )
    assert missing_provider.status_code == 404
    assert missing_provider.json()["code"] == "E-2006"

    missing_job = client.get("/api/v1/jobs/missing-job")
    assert missing_job.status_code == 404
    assert missing_job.json()["code"] == "E-2004"

    missing_export = client.get("/api/v1/exports/missing-export")
    assert missing_export.status_code == 404
    assert missing_export.json()["code"] == "E-2005"

