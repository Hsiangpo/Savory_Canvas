from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest

from conftest import create_generation_job, create_session, create_style, setup_model_routing, wait_for_job_end

PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6p9x8AAAAASUVORK5CYII="


def test_generation_result_returns_accessible_image_url_and_asset_refs(client):
    setup_model_routing(client)
    session = create_session(client, title="真实生图链路", content_mode="food_scenic")

    text_asset = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "text",
            "content": "法式晚餐，窗边夜景，暖光氛围",
        },
    )
    assert text_asset.status_code == 201
    scenic_asset = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "scenic_name",
            "content": "窗边夜景餐桌",
        },
    )
    assert scenic_asset.status_code == 201

    style = create_style(client, session["id"], {"painting_style": ["电影写实"], "color_mood": ["暖金氛围"]})
    job = create_generation_job(client, session["id"], style["id"], image_count=2)
    ended = wait_for_job_end(client, job["id"])
    assert ended["status"] in {"success", "partial_success"}

    result_response = client.get(f"/api/v1/jobs/{job['id']}/results")
    assert result_response.status_code == 200
    result_body = result_response.json()
    assert result_body["images"]

    first_image = result_body["images"][0]
    assert first_image["image_url"].startswith(("http://", "https://"))
    assert not first_image["image_url"].endswith(".txt")
    assert first_image["asset_refs"]

    image_path = urlparse(first_image["image_url"]).path
    image_response = client.get(image_path)
    assert image_response.status_code == 200
    assert image_response.headers.get("content-type", "").startswith("image/")


def test_generation_stages_and_asset_breakdown_observable(client):
    setup_model_routing(client)
    session = create_session(client, title="可观测性链路", content_mode="food")

    asset_response = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "food_name",
            "content": "黄油煎牛排",
        },
    )
    assert asset_response.status_code == 201

    style = create_style(client, session["id"], {"painting_style": ["油画厚涂"]})
    job = create_generation_job(client, session["id"], style["id"], image_count=1)
    ended = wait_for_job_end(client, job["id"])
    assert ended["status"] in {"success", "partial_success"}

    stages_response = client.get(f"/api/v1/jobs/{job['id']}/stages")
    assert stages_response.status_code == 200
    stages_body = stages_response.json()
    assert stages_body["job_id"] == job["id"]
    stage_names = [item["stage"] for item in stages_body["items"]]
    for expected_stage in ["asset_extract", "asset_allocate", "prompt_generate", "image_generate", "copy_generate", "finalize"]:
        assert expected_stage in stage_names
    stage_success_pairs = {(item["stage"], item["status"]) for item in stages_body["items"]}
    for expected_stage in ["asset_extract", "asset_allocate", "prompt_generate", "image_generate", "copy_generate", "finalize"]:
        assert (expected_stage, "success") in stage_success_pairs
    image_stage_messages = [item["stage_message"] for item in stages_body["items"] if item["stage"] == "image_generate"]
    assert any("正在生成图片（" in message for message in image_stage_messages)

    breakdown_response = client.get(f"/api/v1/jobs/{job['id']}/asset-breakdown")
    assert breakdown_response.status_code == 200
    breakdown_body = breakdown_response.json()
    assert breakdown_body["job_id"] == job["id"]
    assert breakdown_body["session_id"] == session["id"]
    assert breakdown_body["content_mode"] == "food"
    assert breakdown_body["source_assets"]
    assert "foods" in breakdown_body["extracted"]
    assert "scenes" in breakdown_body["extracted"]
    assert "keywords" in breakdown_body["extracted"]


def test_generation_observable_endpoints_not_found(client):
    stages_response = client.get("/api/v1/jobs/missing-job/stages")
    assert stages_response.status_code == 404
    assert stages_response.json()["code"] == "E-2004"

    breakdown_response = client.get("/api/v1/jobs/missing-job/asset-breakdown")
    assert breakdown_response.status_code == 404
    assert breakdown_response.json()["code"] == "E-2004"


@contextmanager
def _mock_image_server(mode: str):
    request_count = {"value": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/images/generations":
                self.send_response(404)
                self.end_headers()
                return
            request_count["value"] += 1
            if mode == "http_error":
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": {"message": "上游服务拥堵"}}).encode("utf-8"))
                return
            if mode == "quota_error":
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "error": {
                                "message": (
                                    "预扣费额度失败, 用户[90630]剩余额度: 💰0.108174, "
                                    "需要预扣费额度: 💰0.150000"
                                )
                            }
                        }
                    ).encode("utf-8")
                )
                return
            if mode == "flaky_once":
                if request_count["value"] == 1:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": {"message": "瞬时失败"}}).encode("utf-8"))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"data": [{"b64_json": PNG_BASE64}]}).encode("utf-8"))
                return
            if mode == "invalid_json":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"not-a-json")
                return
            if mode == "data_url":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"data": [{"url": f"data:image/png;base64,{PNG_BASE64}"}]}).encode("utf-8"))
                return
            if mode == "data_string_base64":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"data": [PNG_BASE64]}).encode("utf-8"))
                return
            if mode == "success_with_message":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "message": "请求已处理",
                            "data": [{"b64_json": PNG_BASE64}],
                        }
                    ).encode("utf-8")
                )
                return
            if mode == "non_image_content":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"data": [{"b64_json": "bm90LWFuLWltYWdl"}]}).encode("utf-8"))
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"data": [{"url": "http://example.com/image.png"}]}).encode("utf-8"))

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _setup_routing_with_provider_base_url(client, base_url: str) -> dict:
    provider = client.post(
        "/api/v1/providers",
        json={
            "name": "失败映射测试提供商",
            "base_url": base_url,
            "api_key": "test-key",
            "api_protocol": "responses",
        },
    )
    assert provider.status_code == 201
    provider_payload = provider.json()

    routing = client.post(
        "/api/v1/config/model-routing",
        json={
            "image_model": {
                "provider_id": provider_payload["id"],
                "model_name": "gpt-image-1",
            },
            "text_model": {
                "provider_id": provider_payload["id"],
                "model_name": "gpt-4.1-mini",
            },
        },
    )
    assert routing.status_code == 200
    return provider_payload


def _run_job_expect_e1004(client):
    session = create_session(client, title="失败映射会话", content_mode="food")
    asset_response = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "text",
            "content": "法式牛排与夜景餐桌",
        },
    )
    assert asset_response.status_code == 201
    style = create_style(client, session["id"], {"painting_style": ["电影写实"]})
    job = create_generation_job(client, session["id"], style["id"], image_count=1)
    ended = wait_for_job_end(client, job["id"])
    assert ended["status"] == "failed"
    assert ended["error_code"] == "E-1004"
    return job["id"], ended


def _run_job_expect_success(client):
    session = create_session(client, title="成功映射会话", content_mode="food")
    asset_response = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "text",
            "content": "法式牛排与夜景餐桌",
        },
    )
    assert asset_response.status_code == 201
    style = create_style(client, session["id"], {"painting_style": ["电影写实"]})
    job = create_generation_job(client, session["id"], style["id"], image_count=1)
    ended = wait_for_job_end(client, job["id"])
    assert ended["status"] in {"success", "partial_success"}
    return job["id"], ended


@pytest.mark.image_pipeline_real
def test_generation_upstream_http_error_maps_to_e1004(client):
    with _mock_image_server("http_error") as base_url:
        _setup_routing_with_provider_base_url(client, base_url)
        job_id, job_status = _run_job_expect_e1004(client)
        assert "上游服务拥堵" in job_status["error_message"]
        breakdown_response = client.get(f"/api/v1/jobs/{job_id}/asset-breakdown")
        assert breakdown_response.status_code == 200
        assert breakdown_response.json()["source_assets"]


@pytest.mark.image_pipeline_real
def test_generation_upstream_invalid_json_maps_to_e1004(client):
    with _mock_image_server("invalid_json") as base_url:
        _setup_routing_with_provider_base_url(client, base_url)
        _, job_status = _run_job_expect_e1004(client)
        assert "上游响应格式错误" in job_status["error_message"]


@pytest.mark.image_pipeline_real
def test_generation_quota_error_fails_fast_without_redundant_retries(client):
    with _mock_image_server("quota_error") as base_url:
        _setup_routing_with_provider_base_url(client, base_url)
        job_id, job_status = _run_job_expect_e1004(client)
        assert "预扣费额度失败" in job_status["error_message"]
        stages_response = client.get(f"/api/v1/jobs/{job_id}/stages")
        assert stages_response.status_code == 200
        image_messages = [
            item["stage_message"]
            for item in stages_response.json()["items"]
            if item["stage"] == "image_generate"
        ]
        assert any("第 1 次尝试" in message for message in image_messages)
        assert all("第 2 次尝试" not in message for message in image_messages)


@pytest.mark.image_pipeline_real
def test_generation_upstream_network_error_maps_to_e1004(client):
    _setup_routing_with_provider_base_url(client, "http://127.0.0.1:1")
    _, job_status = _run_job_expect_e1004(client)
    assert "网络异常" in job_status["error_message"] or "图片下载失败" in job_status["error_message"]


@pytest.mark.image_pipeline_real
def test_generation_upstream_non_image_content_maps_to_e1004(client):
    with _mock_image_server("non_image_content") as base_url:
        _setup_routing_with_provider_base_url(client, base_url)
        _, job_status = _run_job_expect_e1004(client)
        assert "非图片内容" in job_status["error_message"]


@pytest.mark.image_pipeline_real
def test_generation_upstream_data_url_success(client):
    with _mock_image_server("data_url") as base_url:
        _setup_routing_with_provider_base_url(client, base_url)
        job_id, _ = _run_job_expect_success(client)
        result_response = client.get(f"/api/v1/jobs/{job_id}/results")
        assert result_response.status_code == 200
        result_body = result_response.json()
        assert result_body["images"]


@pytest.mark.image_pipeline_real
def test_generation_upstream_data_string_base64_success(client):
    with _mock_image_server("data_string_base64") as base_url:
        _setup_routing_with_provider_base_url(client, base_url)
        job_id, _ = _run_job_expect_success(client)
        result_response = client.get(f"/api/v1/jobs/{job_id}/results")
        assert result_response.status_code == 200
        result_body = result_response.json()
        assert result_body["images"]


@pytest.mark.image_pipeline_real
def test_generation_success_payload_with_message_not_misclassified(client):
    with _mock_image_server("success_with_message") as base_url:
        _setup_routing_with_provider_base_url(client, base_url)
        _, ended = _run_job_expect_success(client)
        assert ended["status"] in {"success", "partial_success"}


@pytest.mark.image_pipeline_real
def test_generation_flaky_upstream_can_retry_to_target_count(client):
    with _mock_image_server("flaky_once") as base_url:
        _setup_routing_with_provider_base_url(client, base_url)
        session = create_session(client, title="补位重试会话", content_mode="food")
        asset_response = client.post(
            "/api/v1/assets/text",
            json={
                "session_id": session["id"],
                "asset_type": "text",
                "content": "法式牛排与夜景餐桌",
            },
        )
        assert asset_response.status_code == 201
        style = create_style(client, session["id"], {"painting_style": ["电影写实"]})
        job = create_generation_job(client, session["id"], style["id"], image_count=4)
        ended = wait_for_job_end(client, job["id"])
        assert ended["status"] == "success"
        result = client.get(f"/api/v1/jobs/{job['id']}/results")
        assert result.status_code == 200
        result_body = result.json()
        assert len(result_body["images"]) == 4


def test_generation_partial_success_finalize_has_success_stage(client):
    setup_model_routing(client)
    session = create_session(client, title="partial finalize 会话", content_mode="food")
    asset_response = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "text",
            "content": "法式牛排与夜景餐桌",
        },
    )
    assert asset_response.status_code == 201
    style = create_style(
        client,
        session["id"],
        {"painting_style": ["电影写实"], "force_partial_fail": True},
    )
    job = create_generation_job(client, session["id"], style["id"], image_count=2)
    ended = wait_for_job_end(client, job["id"])
    assert ended["status"] == "partial_success"
    stages = client.get(f"/api/v1/jobs/{job['id']}/stages")
    assert stages.status_code == 200
    finalize_logs = [item for item in stages.json()["items"] if item["stage"] == "finalize"]
    assert any(item["status"] == "success" for item in finalize_logs)
