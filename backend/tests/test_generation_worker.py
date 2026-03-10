from __future__ import annotations

import asyncio
import base64
import io
import json
from datetime import datetime, timezone
from urllib import error as url_error
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
    assert "禁止外圈留白" in prompt_text or "禁止留白边框" in prompt_text
    assert "第1/1张" not in prompt_text
    assert "{'" not in prompt_text


@pytest.mark.real_copy_model
def test_generation_copy_prefers_model_output_when_available_with_real_copy_model_marker(client, monkeypatch):
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
            "transcript_model": {"provider_id": provider["id"], "model_name": "whisper-large-v3-turbo"},
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
        assert timeout in {30, 45, 90}
        if upstream_request.full_url.endswith("/responses"):
            request_body = upstream_request.data.decode("utf-8") if upstream_request.data else ""
            if "资产提取助手" in request_body:
                asset_payload = {
                    "locations": ["呼和浩特"],
                    "scenes": ["大召寺"],
                    "foods": ["烧麦"],
                    "keywords": ["城市美食"],
                    "confidence": 0.9,
                }
                return FakeResponse({"output_text": json.dumps(asset_payload, ensure_ascii=False)})
            if "提示词规划助手" in request_body:
                selected_asset_id = ""
                for line in request_body.splitlines():
                    if "asset_id=" not in line:
                        continue
                    tail = line.split("asset_id=", 1)[1]
                    selected_asset_id = tail.split(";", 1)[0].strip()
                    if selected_asset_id:
                        break
                prompt_payload = {
                    "items": [
                        {
                            "prompt_text": (
                                "请只生成一张图片。\n"
                                "重点呈现食材细节，突出主体质感与暖光氛围。\n"
                                "强约束：禁止拼贴、禁止九宫格、禁止分镜、禁止多画面合成、禁止任何文字水印。"
                            ),
                            "asset_refs": [selected_asset_id] if selected_asset_id else [],
                        }
                    ]
                }
                return FakeResponse({"output_text": json.dumps(prompt_payload, ensure_ascii=False)})
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


def test_build_prompt_specs_prefers_confirmed_allocation_plan(client, monkeypatch):
    worker = client.app.state.services.generation.worker
    breakdown = {
        "source_assets": [
            {"asset_id": "asset-a", "asset_type": "text", "content": "西安钟楼路线"},
            {"asset_id": "asset-b", "asset_type": "text", "content": "肉夹馍与冰峰"},
        ],
        "extracted": {
            "foods": ["肉夹馍", "冰峰"],
            "scenes": ["钟楼"],
            "keywords": ["西安"],
        },
    }
    style = {
        "style_payload": {
            "allocation_plan": [
                {
                    "slot_index": 1,
                    "focus_title": "景点主图",
                    "focus_description": "聚焦钟楼与城市路线。",
                    "locations": ["西安"],
                    "scenes": ["钟楼"],
                    "foods": [],
                    "keywords": ["路线"],
                    "source_asset_ids": ["asset-a"],
                    "confirmed": True,
                },
                {
                    "slot_index": 2,
                    "focus_title": "美食主图",
                    "focus_description": "聚焦肉夹馍与冰峰。",
                    "locations": ["西安"],
                    "scenes": [],
                    "foods": ["肉夹馍", "冰峰"],
                    "keywords": ["美食"],
                    "source_asset_ids": ["asset-b"],
                    "confirmed": True,
                },
            ]
        }
    }

    def fail_if_prompt_plan(*, system_prompt, **_kwargs):
        if "提示词规划助手" in system_prompt:
            raise AssertionError("已确认分图方案时不应再调用提示词规划模型")
        return json.dumps(
            {
                "title": "占位",
                "intro": "占位",
                "guide_sections": [{"heading": "占位", "content": "占位"}],
                "ending": "占位",
                "full_text": "占位",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(worker, "_call_text_model_for_copy", fail_if_prompt_plan)

    specs = worker._build_prompt_specs(
        image_count=2,
        breakdown=breakdown,
        style=style,
        content_mode="food_scenic",
    )
    assert len(specs) == 2
    assert "钟楼" in specs[0]["prompt_text"]
    assert "肉夹馍" in specs[1]["prompt_text"]
    assert "名称+10-20字简短介绍" in specs[0]["prompt_text"]
    assert "禁止出现“（15字）/15字/字数说明”" in specs[0]["prompt_text"]
    assert "图解" in specs[0]["prompt_text"]
    assert "禁止外圈留白" in specs[0]["prompt_text"] or "禁止留白边框" in specs[0]["prompt_text"]
    assert specs[0]["asset_refs"] == ["asset-a"]
    assert specs[1]["asset_refs"] == ["asset-b"]


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


def test_build_image_generation_payload_uses_large_size_for_seedream(client):
    worker = client.app.state.services.generation.worker
    payload = worker._build_image_generation_payload(
        model_name="doubao-seedream-5-0-260128",
        prompt="测试提示词",
        reference_image_paths=[],
    )
    assert payload["size"] == "1920x1920"


@pytest.mark.image_pipeline_real
def test_generate_images_degrades_reference_chain_after_retries(client, monkeypatch):
    worker = client.app.state.services.generation.worker
    saved_files: list[str] = []

    monkeypatch.setattr(worker, "_collect_style_reference_paths", lambda **_: ["https://example.com/style-ref.png"])
    monkeypatch.setattr(worker, "_is_canceled", lambda _job_id: False)
    monkeypatch.setattr(worker, "_advance", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker.storage, "save_generated_image", lambda filename, content: saved_files.append(filename))
    monkeypatch.setattr(worker.result_repo, "add_image", lambda result: result)

    attempts: list[list[str]] = []

    from backend.app.core.errors import DomainError

    def fake_generate_image_binary(*, image_provider, provider_id, model_name, prompt, reference_image_paths=None):
        refs = list(reference_image_paths or [])
        attempts.append(refs)
        if refs:
            raise DomainError(code="E-1004", message="图片生成失败：上游网络异常（上游：Remote end closed connection without response）", status_code=400)
        return base64.b64decode(PNG_BASE64), "png"

    monkeypatch.setattr(worker, "_generate_image_binary", fake_generate_image_binary)

    created_images, failed_images, last_error = asyncio.run(
        worker._generate_images(
            job={"id": "job-reference-fallback", "session_id": "session-reference-fallback"},
            prompt_specs=[{"prompt_text": "请生成一张示例图", "asset_refs": ["asset-1"]}],
            source_assets=[],
            style={"style_payload": {}},
            image_provider={"id": "provider-1", "base_url": "https://example.com/v1", "api_key": "secret"},
            image_model_name="gpt-image-1",
            allow_image_reference=True,
        )
    )

    assert failed_images == 0
    assert last_error is not None
    assert len(created_images) == 1
    assert attempts[0] == ["https://example.com/style-ref.png"]
    assert attempts[-1] == []
    assert saved_files == ["job-reference-fallback_1.png"]


@pytest.mark.image_pipeline_real
def test_generate_image_binary_retries_with_larger_size_when_upstream_requires_min_pixels(client, monkeypatch):
    worker = client.app.state.services.generation.worker
    worker_module = _import_generation_worker_module()

    payloads: list[dict] = []

    def fake_post_json(*, provider_id, model_name, url, api_key, payload):
        payloads.append(payload)
        if len(payloads) == 1:
            raise worker_module.DomainError(
                code="E-1004",
                message=(
                    "图片生成失败：The parameter `size` specified in the request is not valid: "
                    "image size must be at least 3686400 pixels."
                ),
                status_code=400,
            )
        return {"data": [{"b64_json": PNG_BASE64}]}

    monkeypatch.setattr(worker, "_post_json", fake_post_json)
    image_bytes, extension = worker._generate_image_binary(
        image_provider={"id": "provider-x", "base_url": "https://example.com/v1", "api_key": "test"},
        provider_id="provider-x",
        model_name="gpt-image-1",
        prompt="请生成一张示例图",
        reference_image_paths=None,
    )
    assert extension == "png"
    assert image_bytes == base64.b64decode(PNG_BASE64)
    assert len(payloads) == 2
    assert payloads[0]["size"] == "1024x1024"
    assert payloads[1]["size"] == "1920x1920"


def test_postprocess_generated_image_trims_uniform_outer_border(client):
    pytest.importorskip("PIL")
    from PIL import Image

    worker = client.app.state.services.generation.worker
    canvas = Image.new("RGB", (140, 140), (235, 220, 198))
    content = Image.new("RGB", (96, 96), (200, 88, 66))
    canvas.paste(content, (22, 22))
    raw_bytes = io.BytesIO()
    canvas.save(raw_bytes, format="PNG")

    processed_bytes, processed_extension = worker._postprocess_generated_image(raw_bytes.getvalue(), "png")
    assert processed_extension == "png"
    with Image.open(io.BytesIO(processed_bytes)) as processed:
        assert processed.size[0] < 140
        assert processed.size[1] < 140
        assert processed.size[0] >= 96
        assert processed.size[1] >= 96


def test_collect_style_reference_paths_only_uses_style_reference_sources(client):
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
    assert file_path_c in refs
    assert file_path_b not in refs


def test_generation_copy_failure_uses_fallback_copy(client, monkeypatch):
    setup_model_routing(client)
    session = create_session(client, title="文案失败保留图片", content_mode="food")
    asset_response = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "text",
            "content": "西安羊肉泡馍与钟楼夜景",
        },
    )
    assert asset_response.status_code == 201
    style = create_style(client, session["id"], {"painting_style": ["手绘插画"]})

    worker = client.app.state.services.generation.worker
    worker_module = _import_generation_worker_module()

    def fake_generate_copy_result(*args, **kwargs):
        raise worker_module.DomainError(code="E-1004", message="文案生成失败：上游超时", status_code=503)

    monkeypatch.setattr(worker, "_generate_copy_result", fake_generate_copy_result)

    job = create_generation_job(client, session["id"], style["id"], image_count=1)
    ended = wait_for_job_end(client, job["id"])
    assert ended["status"] == "success"
    assert ended.get("error_code") in (None, "")

    result_response = client.get(f"/api/v1/jobs/{job['id']}/results")
    assert result_response.status_code == 200
    payload = result_response.json()
    assert payload["images"]
    assert payload["copy"]["title"]
    assert payload["copy"]["full_text"]
    assert len(payload["copy"]["guide_sections"]) >= 3


def test_generation_copy_result_retries_with_strict_json_prompt(client, monkeypatch):
    worker = client.app.state.services.generation.worker
    call_history: list[str] = []

    def fake_resolve_provider():
        return (
            {
                "id": "provider-copy-retry",
                "base_url": "https://copy-retry.local/v1",
                "api_key": "copy-retry-key",
                "api_protocol": "responses",
            },
            "gpt-4.1-mini",
        )

    def fake_call_text_model_for_copy(*, provider, model_name, system_prompt, user_prompt):
        call_history.append(system_prompt)
        if len(call_history) == 1:
            return "文案输出失败，需要重试"
        return json.dumps(
            {
                "title": "西安双图发布方案",
                "intro": "本次内容围绕西安景点与美食双主题展开，风格统一且叙事清晰。",
                "guide_sections": [
                    {"heading": "景点图重点", "content": "第一张聚焦钟楼与城市动线，通过手账布局强化路线可读性与地标识别。"},
                    {"heading": "美食图重点", "content": "第二张突出肉夹馍与冰峰细节，加入手绘标签与信息块增强食欲表达。"},
                    {"heading": "发布建议", "content": "文案建议按路线先后组织，结尾加入实用建议与互动问题提升收藏率。"},
                ],
                "ending": "保持统一复古手账语气与色调，有利于账号视觉一致性沉淀。",
                "full_text": "西安双图发布方案\n...",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(worker, "_resolve_text_model_provider", fake_resolve_provider)
    monkeypatch.setattr(worker, "_call_text_model_for_copy", fake_call_text_model_for_copy)

    result = worker._generate_copy_result(
        job={"id": "job-copy-retry"},
        style={"name": "复古旅行手账风格", "style_payload": {"painting_style": "手绘水彩"}},
        images=[
            {"image_index": 1, "prompt_text": "生成一张西安景点主题海报"},
            {"image_index": 2, "prompt_text": "生成一张西安美食主题海报"},
        ],
        content_mode="food_scenic",
        breakdown={
            "extracted": {
                "foods": ["肉夹馍", "冰峰"],
                "scenes": ["钟楼", "兵马俑"],
                "keywords": ["西安", "路线"],
            }
        },
    )

    assert result["title"] == "西安双图发布方案"
    assert len(result["guide_sections"]) >= 3
    assert len(call_history) == 2
    assert "必须只输出一个 JSON 对象" in call_history[1]


def test_copy_text_model_retries_base_model_when_thinking_variant_conflicts(client, monkeypatch):
    worker = client.app.state.services.generation.worker
    provider = {
        "id": "provider-demo",
        "base_url": "https://example.com/v1",
        "api_key": "secret",
        "api_protocol": "responses",
    }
    called_models: list[str] = []

    class FakeResponse:
        def __init__(self, payload: dict):
            self._payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(upstream_request, timeout=45):
        assert timeout in {45, 90}
        payload = json.loads((upstream_request.data or b"{}").decode("utf-8", errors="ignore"))
        model = str(payload.get("model") or "")
        called_models.append(model)
        if model == "gemini-3-pro-preview-thinking-low":
            body = '{"error":{"message":"You can only set only one of thinking budget and thinking level.","type":"new_api_error"}}'
            raise url_error.HTTPError(
                url=upstream_request.full_url,
                code=400,
                msg="Bad Request",
                hdrs=None,
                fp=io.BytesIO(body.encode("utf-8")),
            )
        return FakeResponse({"output_text": "文案生成成功"})

    from backend.app.workers.generation import pipeline_mixin as pipeline_module

    monkeypatch.setattr(pipeline_module.request, "urlopen", fake_urlopen)
    from backend.app.workers.generation.pipeline_mixin import GenerationPipelineMixin

    content = GenerationPipelineMixin._call_text_model_for_copy(
        worker,
        provider=provider,
        model_name="gemini-3-pro-preview-thinking-low",
        system_prompt="系统提示词",
        user_prompt="用户提示词",
    )
    assert content == "文案生成成功"
    assert called_models == ["gemini-3-pro-preview-thinking-low", "gemini-3-pro-preview"]


def test_generation_copy_failure_falls_back_to_template(client, monkeypatch):
    setup_model_routing(client)
    session = create_session(client, title="文案兜底成功", content_mode="food_scenic")
    asset_response = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "text",
            "content": "西安老菜场、中流巷、书院门、德福巷四个地点的城市漫游攻略",
        },
    )
    assert asset_response.status_code == 201
    style = create_style(client, session["id"], {"painting_style": ["手绘插画"]})

    worker = client.app.state.services.generation.worker
    worker_module = _import_generation_worker_module()

    def fake_generate_copy_result(*args, **kwargs):
        raise worker_module.DomainError(code="E-1004", message="文案生成失败：上游 502", status_code=503)

    monkeypatch.setattr(worker, "_generate_copy_result", fake_generate_copy_result)

    job = create_generation_job(client, session["id"], style["id"], image_count=1)
    ended = wait_for_job_end(client, job["id"])
    assert ended["status"] == "success"
    assert ended.get("error_code") in (None, "")

    result_response = client.get(f"/api/v1/jobs/{job['id']}/results")
    assert result_response.status_code == 200
    payload = result_response.json()
    assert payload["images"]
    assert payload["copy"]["title"]
    assert payload["copy"]["full_text"]
    assert len(payload["copy"]["guide_sections"]) >= 3
