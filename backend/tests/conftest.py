from __future__ import annotations

import base64
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch, request):
    monkeypatch.setenv("SAVORY_CANVAS_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SAVORY_CANVAS_STORAGE_DIR", str(tmp_path / "storage"))

    from backend.app.services.model_service import ModelService
    from backend.app.services.style_service import StyleService
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

    def fake_call_text_model_for_copy(
        _self,
        *,
        provider: dict[str, Any],
        model_name: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        _ = provider, model_name
        if "资产提取助手" in system_prompt:
            payload = {
                "locations": ["示例地点"],
                "scenes": ["示例景点"],
                "foods": ["示例美食"],
                "keywords": ["示例关键词"],
                "confidence": 0.92,
            }
            return json.dumps(payload, ensure_ascii=False)
        if "提示词规划助手" in system_prompt:
            count = 1
            selected_asset_id = ""
            for line in user_prompt.splitlines():
                if not line.startswith("目标张数："):
                    if "asset_id=" in line and not selected_asset_id:
                        tail = line.split("asset_id=", 1)[1]
                        selected_asset_id = tail.split(";", 1)[0].strip()
                    continue
                raw = line.split("：", 1)[1].strip()
                if raw.isdigit():
                    count = max(1, min(10, int(raw)))
            if "内容模式：scenic" in user_prompt:
                core_line = "重点呈现场景氛围，突出地标与路线叙事。"
            elif "内容模式：food_scenic" in user_prompt:
                core_line = "平衡食材与场景，统一风格并保持叙事连贯。"
            else:
                core_line = "重点呈现食材细节，突出质感与可食性。"
            items = [
                {
                    "prompt_text": (
                        "请只生成一张图片。\n"
                        f"{core_line}\n"
                        "强约束：禁止拼贴、禁止九宫格、禁止分镜、禁止多画面合成、禁止任何文字水印。"
                    ),
                    "asset_refs": [selected_asset_id] if selected_asset_id else [],
                }
                for _ in range(count)
            ]
            return json.dumps({"items": items}, ensure_ascii=False)
        if "内容模式：scenic" in user_prompt:
            heading = "场景观察"
            mode_title = "场景发布指南"
        elif "内容模式：food_scenic" in user_prompt:
            heading = "食材与场景协调"
            mode_title = "混合发布指南"
        else:
            heading = "准备食材"
            mode_title = "美食发布指南"
        payload = {
            "title": mode_title,
            "intro": "已根据图片与资产信息生成可发布文案结构。",
            "guide_sections": [
                {"heading": heading, "content": "请先明确内容主线与画面核心，再按平台节奏组织信息层次。"},
                {"heading": "内容节奏", "content": "建议采用开场钩子、核心信息、互动引导三段结构，提升阅读完成度。"},
                {"heading": "发布建议", "content": "发布时补充标签与地点信息，并保持语气统一，增强账号内容一致性。"},
            ],
            "ending": "建议配合统一视觉语气进行连续发布，提升系列内容辨识度。",
            "full_text": "示例完整文案",
        }
        return json.dumps(payload, ensure_ascii=False)

    def fake_call_text_model_with_images(
        self,
        provider: dict[str, Any],
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        image_urls: list[str],
        *,
        strict_json: bool,
    ) -> str:
        _ = image_urls
        return self._call_text_model(
            provider,
            model_name,
            system_prompt,
            user_prompt,
            strict_json=strict_json,
        )

    monkeypatch.setattr(ModelService, "fetch_provider_models", fake_fetch_provider_models)
    monkeypatch.setattr(StyleService, "_call_text_model_with_images", fake_call_text_model_with_images)
    if not request.node.get_closest_marker("image_pipeline_real"):
        monkeypatch.setattr(GenerationWorker, "_generate_image_binary", fake_generate_image_binary)
    if not request.node.get_closest_marker("real_copy_model"):
        monkeypatch.setattr(GenerationWorker, "_call_text_model_for_copy", fake_call_text_model_for_copy)

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
