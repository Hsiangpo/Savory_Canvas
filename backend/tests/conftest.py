from __future__ import annotations

import base64
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch, request):
    monkeypatch.setenv("SAVORY_CANVAS_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SAVORY_CANVAS_STORAGE_DIR", str(tmp_path / "storage"))

    from backend.app.services.model_service import ModelService
    from backend.app.workers.generation_worker import GenerationWorker

    def fake_fetch_provider_models(_, __):
        return [
            {"id": "gpt-image-1", "name": "gpt-image-1", "capabilities": ["image_generation"]},
            {"id": "gpt-4.1-mini", "name": "gpt-4.1-mini", "capabilities": ["text_generation"]},
            {"id": "gpt-4.1", "name": "gpt-4.1", "capabilities": ["text_generation", "vision"]},
        ]

    def fake_generate_image_binary(_self, **_kwargs):
        png_bytes = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6p9x8AAAAASUVORK5CYII=")
        return png_bytes, "png"

    monkeypatch.setattr(ModelService, "fetch_provider_models", fake_fetch_provider_models)
    if not request.node.get_closest_marker("image_pipeline_real"):
        monkeypatch.setattr(GenerationWorker, "_generate_image_binary", fake_generate_image_binary)

    from backend.app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    return None


def create_session(client: TestClient, title: str = "会话", content_mode: str = "food") -> dict[str, Any]:
    response = client.post(
        "/api/v1/sessions",
        json={"title": title, "content_mode": content_mode},
    )
    assert response.status_code == 201
    return response.json()


def create_provider(client: TestClient, name: str = "默认提供商") -> dict[str, Any]:
    response = client.post(
        "/api/v1/providers",
        json={
            "name": name,
            "base_url": "https://example.com",
            "api_key": "secret-key",
            "api_protocol": "responses",
        },
    )
    assert response.status_code == 201
    return response.json()


def setup_model_routing(
    client: TestClient,
    *,
    image_model_name: str = "gpt-image-1",
    text_model_name: str = "gpt-4.1-mini",
) -> dict[str, Any]:
    provider = create_provider(client)
    response = client.post(
        "/api/v1/config/model-routing",
        json={
            "image_model": {
                "provider_id": provider["id"],
                "model_name": image_model_name,
            },
            "text_model": {
                "provider_id": provider["id"],
                "model_name": text_model_name,
            },
        },
    )
    assert response.status_code == 200
    return provider


def create_style(client: TestClient, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    style_payload = {
        "painting_style": "手绘插画",
        "color_mood": "温暖治愈",
        "prompt_example": "请保持统一风格与清晰图文布局。",
        "style_prompt": "请保持统一风格与清晰图文布局。",
        "sample_image_asset_id": None,
        "extra_keywords": [],
    }
    style_payload.update(payload)
    response = client.post(
        "/api/v1/styles",
        json={"session_id": session_id, "name": "风格模板", "style_payload": style_payload},
    )
    assert response.status_code == 201
    return response.json()


def create_generation_job(
    client: TestClient,
    session_id: str,
    style_profile_id: str,
    image_count: int,
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/jobs/generate",
        json={
            "session_id": session_id,
            "style_profile_id": style_profile_id,
            "image_count": image_count,
        },
    )
    assert response.status_code == 202
    return response.json()


def wait_for_job_end(client: TestClient, job_id: str) -> dict[str, Any]:
    final_statuses = {"success", "partial_success", "failed", "canceled"}

    def _poll():
        response = client.get(f"/api/v1/jobs/{job_id}")
        if response.status_code != 200:
            return None
        data = response.json()
        if data["status"] in final_statuses:
            return data
        return None

    result = wait_until(_poll, timeout=8.0, interval=0.1)
    assert result is not None
    return result


def wait_for_export_end(client: TestClient, export_id: str) -> dict[str, Any]:
    final_statuses = {"success", "failed"}

    def _poll():
        response = client.get(f"/api/v1/exports/{export_id}")
        if response.status_code != 200:
            return None
        data = response.json()
        if data["status"] in final_statuses:
            return data
        return None

    result = wait_until(_poll, timeout=8.0, interval=0.1)
    assert result is not None
    return result
