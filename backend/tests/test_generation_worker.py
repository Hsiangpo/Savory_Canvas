from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from conftest import (
    create_generation_job,
    create_session,
    create_style,
    setup_model_routing,
    wait_for_job_end,
)

PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6p9x8AAAAASUVORK5CYII="


def _import_generation_worker_module():
    try:
        from backend.app.workers import generation_worker as worker_module
    except ModuleNotFoundError:
        from app.workers import generation_worker as worker_module
    return worker_module


@pytest.mark.parametrize(
    (
        "content_mode",
        "asset_type",
        "asset_content",
        "expected_heading",
        "unexpected_heading",
        "expected_prompt_fragment",
        "unexpected_prompt_fragment",
    ),
    [
        ("food", "food_name", "黄油煎牛排", "准备食材", "场景观察", "重点呈现食材细节", "重点呈现场景氛围"),
        ("scenic", "scenic_name", "海边日落餐桌", "场景观察", "准备食材", "重点呈现场景氛围", "重点呈现食材细节"),
        ("food_scenic", "text", "法式烛光晚餐，窗边晚霞", "食材与场景协调", None, "平衡食材与场景", None),
    ],
)
def test_generation_copy_result_respects_content_mode(
    client,
    content_mode: str,
    asset_type: str,
    asset_content: str,
    expected_heading: str,
    unexpected_heading: str | None,
    expected_prompt_fragment: str,
    unexpected_prompt_fragment: str | None,
):
    setup_model_routing(client)
    session = create_session(client, title=f"{content_mode}-会话", content_mode=content_mode)
    asset_response = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": asset_type,
            "content": asset_content,
        },
    )
    assert asset_response.status_code == 201

    style = create_style(client, session["id"], {"painting_style": ["电影写实"]})
    job = create_generation_job(client, session["id"], style["id"], image_count=1)
    ended = wait_for_job_end(client, job["id"])
    assert ended["status"] in {"success", "partial_success"}

    result_response = client.get(f"/api/v1/jobs/{job['id']}/results")
    assert result_response.status_code == 200
    copy_result = result_response.json()["copy"]
    headings = [item["heading"] for item in copy_result["guide_sections"]]
    assert expected_heading in headings
    if unexpected_heading:
        assert unexpected_heading not in headings

    image_results = result_response.json()["images"]
    assert image_results
    prompt_text = image_results[0]["prompt_text"]
    assert expected_prompt_fragment in prompt_text
    if unexpected_prompt_fragment:
        assert unexpected_prompt_fragment not in prompt_text
    assert "请只生成一张图片" in prompt_text
    assert "禁止拼贴" in prompt_text
    assert "第1/1张" not in prompt_text
    assert "{'" not in prompt_text


def test_generation_copy_prefers_model_output_when_available(client, monkeypatch):
    provider_response = client.post(
        "/api/v1/providers",
        json={
            "name": "文案模型测试提供商",
            "base_url": "https://copy-test.local/v1",
            "api_key": "copy-key",
            "api_protocol": "responses",
        },
    )
    assert provider_response.status_code == 201
    provider = provider_response.json()
    routing_response = client.post(
        "/api/v1/config/model-routing",
        json={
            "image_model": {"provider_id": provider["id"], "model_name": "gpt-image-1"},
            "text_model": {"provider_id": provider["id"], "model_name": "gpt-4.1-mini"},
        },
    )
    assert routing_response.status_code == 200

    session = create_session(client, title="文案模型测试", content_mode="food")
    asset_response = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "text",
            "content": "黄油煎牛排与暖光餐桌",
        },
    )
    assert asset_response.status_code == 201
    style = create_style(client, session["id"], {"painting_style": ["电影写实"]})

    worker_module = _import_generation_worker_module()

    class FakeResponse:
        def __init__(self, payload: dict):
            self._payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(upstream_request, timeout=30):
        assert timeout == 30
        if upstream_request.full_url.endswith("/responses"):
            copy_payload = {
                "title": "电影感牛排内容发布指南",
                "intro": "本轮图像呈现了高对比暖光与牛排质感，适合做高完成度餐饮内容发布。",
                "guide_sections": [
                    {"heading": "画面亮点", "content": "主体油脂反光与木质桌面纹理形成层次，镜头焦点稳定，适合封面首图。"},
                    {"heading": "发布结构", "content": "建议先用情绪化开场引导，再补关键烹饪节点，最后加入互动问题提升评论率。"},
                    {"heading": "转化建议", "content": "可在结尾追加食材替换方案和火候阈值，提升收藏与复刻意愿。"},
                ],
                "ending": "建议配合统一暖色调滤镜并保持短句节奏，提升平台分发稳定性。",
                "full_text": "电影感牛排内容发布指南\\n...",
            }
            return FakeResponse({"output_text": json.dumps(copy_payload, ensure_ascii=False)})
        raise AssertionError(f"unexpected endpoint: {upstream_request.full_url}")

    monkeypatch.setattr(worker_module.request, "urlopen", fake_urlopen)

    job = create_generation_job(client, session["id"], style["id"], image_count=1)
    ended = wait_for_job_end(client, job["id"])
    assert ended["status"] in {"success", "partial_success"}

    result_response = client.get(f"/api/v1/jobs/{job['id']}/results")
    assert result_response.status_code == 200
    copy_result = result_response.json()["copy"]
    assert copy_result["title"] == "电影感牛排内容发布指南"
    assert len(copy_result["guide_sections"]) >= 3
    assert copy_result["guide_sections"][0]["heading"] == "画面亮点"


def test_generation_prompt_ignores_non_visual_style_keys(client):
    setup_model_routing(client)
    session = create_session(client, title="风格字段过滤会话", content_mode="food")
    asset_response = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "text",
            "content": "黄油煎牛排与暖光餐桌",
        },
    )
    assert asset_response.status_code == 201
    style = create_style(
        client,
        session["id"],
        {
            "painting_style": ["电影写实"],
            "image_count": ["4张"],
            "style_prompt": "调试文案",
            "force_partial_fail": False,
        },
    )
    job = create_generation_job(client, session["id"], style["id"], image_count=1)
    ended = wait_for_job_end(client, job["id"])
    assert ended["status"] in {"success", "partial_success"}
    result_response = client.get(f"/api/v1/jobs/{job['id']}/results")
    assert result_response.status_code == 200
    prompt_text = result_response.json()["images"][0]["prompt_text"]
    assert "image_count" not in prompt_text
    assert "style_prompt" not in prompt_text
    assert "force_partial_fail" not in prompt_text


@pytest.mark.image_pipeline_real
def test_generate_image_binary_retry_without_references_when_upstream_rejects(client, monkeypatch):
    worker = client.app.state.services.generation.worker
    worker_module = _import_generation_worker_module()

    payloads: list[dict] = []

    def fake_post_json(*, provider_id, model_name, url, api_key, payload):
        payloads.append(payload)
        if len(payloads) == 1:
            raise worker_module.DomainError(code="E-1004", message="图片生成失败：unsupported input_images", status_code=400)
        return {"data": [{"b64_json": PNG_BASE64}]}

    monkeypatch.setattr(worker, "_post_json", fake_post_json)
    image_bytes, extension = worker._generate_image_binary(
        image_provider={"id": "provider-x", "base_url": "https://example.com/v1", "api_key": "test"},
        provider_id="provider-x",
        model_name="gpt-image-1",
        prompt="请生成一张示例图",
        reference_image_paths=["https://example.com/reference.png"],
    )
    assert extension == "png"
    assert image_bytes == base64.b64decode(PNG_BASE64)
    assert len(payloads) == 2
    assert "input_images" in payloads[0]
    assert "input_images" not in payloads[1]


def test_collect_style_reference_paths_includes_sample_and_named_reference_assets(client):
    worker = client.app.state.services.generation.worker
    session = create_session(client, title="参考图识别会话", content_mode="food")
    file_path_a = client.app.state.storage.save_image(f"{session['id']}_sample_a.png", base64.b64decode(PNG_BASE64))
    file_path_b = client.app.state.storage.save_image(f"{session['id']}_sample_b.png", base64.b64decode(PNG_BASE64))
    file_path_c = client.app.state.storage.save_image(f"{session['id']}_sample_c.png", base64.b64decode(PNG_BASE64))
    image_a = worker.asset_repo.create(
        {
            "id": str(uuid4()),
            "session_id": session["id"],
            "asset_type": "image",
            "content": "风格参考图.png",
            "file_path": file_path_a,
            "status": "ready",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    worker.asset_repo.create(
        {
            "id": str(uuid4()),
            "session_id": session["id"],
            "asset_type": "image",
            "content": "内容素材图.png",
            "file_path": file_path_b,
            "status": "ready",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    image_c = worker.asset_repo.create(
        {
            "id": str(uuid4()),
            "session_id": session["id"],
            "asset_type": "image",
            "content": "临时上传图.png",
            "file_path": file_path_c,
            "status": "ready",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    worker.inspiration_repo.add_message(
        {
            "id": str(uuid4()),
            "session_id": session["id"],
            "role": "user",
            "content": "上传参考图片",
            "attachments": [
                {
                    "id": image_c["id"],
                    "asset_id": image_c["id"],
                    "type": "image",
                    "name": "临时上传图.png",
                    "status": "ready",
                    "usage_type": "style_reference",
                }
            ],
            "options": None,
            "asset_candidates": None,
            "style_context": None,
            "stage": "style_collecting",
            "fallback_used": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    refs = worker._collect_style_reference_paths(
        session_id=session["id"],
        source_assets=worker.asset_repo.list_by_session(session["id"]),
        style_payload={"sample_image_asset_id": image_a["id"]},
        allow_image_reference=True,
    )
    assert file_path_a in refs
    assert file_path_b not in refs
    assert file_path_c in refs
