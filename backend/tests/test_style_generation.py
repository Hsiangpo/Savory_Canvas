from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from uuid import uuid4
from urllib import error as url_error

from conftest import (
    create_generation_job,
    create_session,
    create_style,
    setup_model_routing,
    wait_for_job_end,
)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDATx\xdac\xfc\xff\x1f\x00"
    b"\x03\x03\x02\x00\xee\xa9\xf7\x1f\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _insert_image_asset(client, session_id: str, file_name: str = "style-sample.png") -> dict:
    storage = client.app.state.storage
    asset_repo = client.app.state.services.inspiration.asset_repo
    file_path = storage.save_image(f"{session_id}_{file_name}", PNG_BYTES)
    return asset_repo.create(
        {
            "id": str(uuid4()),
            "session_id": session_id,
            "asset_type": "image",
            "content": file_name,
            "file_path": file_path,
            "status": "ready",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def test_style_chat_and_profile_crud(client):
    session = create_session(client)

    init_resp = client.post(
        "/api/v1/styles/chat",
        json={
            "session_id": session["id"],
            "stage": "init",
            "user_reply": "先给我推荐风格",
            "selected_items": [],
        },
    )
    assert init_resp.status_code == 200
    init_data = init_resp.json()
    assert init_data["stage"] == "painting_style"
    assert init_data["next_stage"] == "background_decor"

    chat_resp = client.post(
        "/api/v1/styles/chat",
        json={
            "session_id": session["id"],
            "stage": "painting_style",
            "user_reply": "我想要油画质感",
            "selected_items": [],
        },
    )
    assert chat_resp.status_code == 200
    chat_data = chat_resp.json()
    assert "reply" in chat_data
    assert "options" in chat_data
    assert "fallback_used" in chat_data

    create_resp = client.post(
        "/api/v1/styles",
        json={
            "session_id": session["id"],
            "name": "暖色餐桌",
            "style_payload": {
                "painting_style": "油画",
                "color_mood": "暖金氛围",
                "prompt_example": "请保持复古温暖的旅行手账质感。",
                "style_prompt": "请保持复古温暖的旅行手账质感。",
                "sample_image_asset_id": None,
                "extra_keywords": [],
            },
        },
    )
    assert create_resp.status_code == 201
    profile = create_resp.json()

    list_resp = client.get("/api/v1/styles")
    assert list_resp.status_code == 200
    assert len(list_resp.json()["items"]) == 1

    patch_resp = client.patch(
        f"/api/v1/styles/{profile['id']}",
        json={"name": "暖色餐桌升级"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "暖色餐桌升级"

    delete_resp = client.delete(f"/api/v1/styles/{profile['id']}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] is True


def test_style_chat_dynamic_options_change_with_input(client, monkeypatch):
    setup_model_routing(client)
    session = create_session(client)

    from backend.app.services.style_service import StyleService

    def fake_model_text(*_args, **_kwargs):
        user_prompt = _args[4]
        if "油画" in user_prompt:
            return (
                '{"reply":"已根据你的偏好生成候选。",'
                '"options":{"title":"请选择绘画风格","items":["油画厚涂","复古胶片","电影写实"],"max":2}}'
            )
        return (
            '{"reply":"已根据你的偏好生成候选。",'
            '"options":{"title":"请选择绘画风格","items":["水彩晕染","清新插画","晨光电影感"],"max":2}}'
        )

    monkeypatch.setattr(StyleService, "_call_text_model", fake_model_text)

    response_a = client.post(
        "/api/v1/styles/chat",
        json={
            "session_id": session["id"],
            "stage": "painting_style",
            "user_reply": "我偏好油画和复古感",
            "selected_items": [],
        },
    )
    assert response_a.status_code == 200
    body_a = response_a.json()
    assert body_a["fallback_used"] is False
    assert body_a["stage"] == "painting_style"

    response_b = client.post(
        "/api/v1/styles/chat",
        json={
            "session_id": session["id"],
            "stage": "painting_style",
            "user_reply": "我想要清新水彩和明亮氛围",
            "selected_items": [],
        },
    )
    assert response_b.status_code == 200
    body_b = response_b.json()
    assert body_b["fallback_used"] is False
    assert body_b["stage"] == "painting_style"
    assert body_a["options"]["items"] != body_b["options"]["items"]


def test_style_chat_fallback_when_model_output_invalid_json(client, monkeypatch):
    setup_model_routing(client)
    session = create_session(client)

    from backend.app.services.style_service import StyleService

    def invalid_json_payload(*_args, **_kwargs):
        return "{invalid-json"

    monkeypatch.setattr(StyleService, "_call_text_model", invalid_json_payload)
    response = client.post(
        "/api/v1/styles/chat",
        json={
            "session_id": session["id"],
            "stage": "painting_style",
            "user_reply": "请给我风格建议",
            "selected_items": [],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["fallback_used"] is True
    assert "降级" in body["reply"] or "默认" in body["reply"]
    assert len(body["options"]["items"]) >= 1


def test_style_chat_fallback_when_model_unavailable(client):
    session = create_session(client)
    response = client.post(
        "/api/v1/styles/chat",
        json={
            "session_id": session["id"],
            "stage": "painting_style",
            "user_reply": "我想做电影感画风",
            "selected_items": [],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["fallback_used"] is True
    assert "降级" in body["reply"] or "默认" in body["reply"]
    assert body["stage"] == "painting_style"


def test_style_chat_progress_to_next_stage_for_multi_select(client, monkeypatch):
    setup_model_routing(client)
    session = create_session(client)

    from backend.app.services.style_service import StyleService

    def fake_next_stage_payload(*_args, **_kwargs):
        return (
            '{"reply":"进入下一阶段。",'
            '"options":{"title":"请选择背景装饰","items":["暖光餐桌","窗边光影","花束点缀"],"max":2}}'
        )

    monkeypatch.setattr(StyleService, "_call_text_model", fake_next_stage_payload)

    response = client.post(
        "/api/v1/styles/chat",
        json={
            "session_id": session["id"],
            "stage": "painting_style",
            "user_reply": "我已经选好风格",
            "selected_items": ["油画厚涂", "电影写实"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["fallback_used"] is False
    assert body["stage"] == "background_decor"
    assert body["next_stage"] == "color_mood"


def test_style_chat_protocol_fallback_to_chat_completions_success(client, monkeypatch):
    setup_model_routing(client)
    session = create_session(client)

    from backend.app.services.style_service import StyleFallbackError, StyleService

    def fake_post_json(_self, url, _payload, _api_key):
        if url.endswith("/responses"):
            raise StyleFallbackError("protocol_endpoint_not_supported", "HTTP 404")
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"reply":"已自动切换协议并生成候选",'
                            '"options":{"title":"请选择绘画风格","items":["油画厚涂","电影写实"],"max":2}}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(StyleService, "_post_json", fake_post_json)
    response = client.post(
        "/api/v1/styles/chat",
        json={
            "session_id": session["id"],
            "stage": "painting_style",
            "user_reply": "我要电影感",
            "selected_items": [],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["fallback_used"] is False
    assert len(body["options"]["items"]) >= 1


def test_style_chat_protocol_fallback_when_responses_returns_500_not_implemented(client, monkeypatch):
    setup_model_routing(client)
    session = create_session(client)

    from backend.app.services import style_service as style_service_module

    class FakeResponse:
        def __init__(self, payload: dict):
            self._payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(upstream_request, timeout=20):
        assert timeout == 20
        target_url = upstream_request.full_url
        if target_url.endswith("/responses"):
            failure_body = (
                '{"error":{"message":"not implemented","type":"new_api_error","code":"convert_request_failed"}}'
            )
            raise url_error.HTTPError(
                url=target_url,
                code=500,
                msg="Internal Server Error",
                hdrs=None,
                fp=io.BytesIO(failure_body.encode("utf-8")),
            )
        if target_url.endswith("/chat/completions"):
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"reply":"协议回退成功",'
                                    '"options":{"title":"请选择绘画风格","items":["电影写实","油画厚涂"],"max":2}}'
                                )
                            }
                        }
                    ]
                }
            )
        raise AssertionError(f"unexpected url: {target_url}")

    monkeypatch.setattr(style_service_module.request, "urlopen", fake_urlopen)
    response = client.post(
        "/api/v1/styles/chat",
        json={
            "session_id": session["id"],
            "stage": "painting_style",
            "user_reply": "我要高级电影感",
            "selected_items": [],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["fallback_used"] is False
    assert body["reply"] == "协议回退成功"
    assert "电影写实" in body["options"]["items"]


def test_style_chat_retry_strict_json_success(client, monkeypatch):
    setup_model_routing(client)
    session = create_session(client)

    from backend.app.services.style_service import StyleService

    called = []

    def fake_call_text_model(_self, _provider, _model_name, _system_prompt, _user_prompt, *, strict_json):
        called.append(strict_json)
        if not strict_json:
            return "{invalid-json"
        return (
            '{"reply":"严格 JSON 重试成功",'
            '"options":{"title":"请选择绘画风格","items":["油画厚涂","复古胶片"],"max":2}}'
        )

    monkeypatch.setattr(StyleService, "_call_text_model", fake_call_text_model)
    response = client.post(
        "/api/v1/styles/chat",
        json={
            "session_id": session["id"],
            "stage": "painting_style",
            "user_reply": "给我风格候选",
            "selected_items": [],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["fallback_used"] is False
    assert body["reply"] == "严格 JSON 重试成功"
    assert called == [False, True]


def test_style_chat_fallback_after_both_protocols_failed(client, monkeypatch, caplog):
    setup_model_routing(client)
    session = create_session(client)

    from backend.app.services.style_service import StyleFallbackError, StyleService

    def always_fail(_self, _url, _payload, _api_key):
        raise StyleFallbackError("protocol_endpoint_not_supported", "HTTP 404")

    monkeypatch.setattr(StyleService, "_post_json", always_fail)
    caplog.set_level("WARNING")

    response = client.post(
        "/api/v1/styles/chat",
        json={
            "session_id": session["id"],
            "stage": "painting_style",
            "user_reply": "测试协议失败降级",
            "selected_items": [],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["fallback_used"] is True
    assert "风格对话降级" in caplog.text
    assert "protocol_both_failed" in caplog.text


def test_generation_job_success_and_result(client):
    setup_model_routing(client)
    session = create_session(client)

    asset_resp = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "text",
            "content": "主食是牛排，想要电影感的厨房场景",
        },
    )
    assert asset_resp.status_code == 201

    style = create_style(client, session["id"], {"painting_style": ["电影感"]})
    job = create_generation_job(client, session["id"], style["id"], image_count=2)

    ended = wait_for_job_end(client, job["id"])
    assert ended["status"] in {"success", "partial_success"}

    result_resp = client.get(f"/api/v1/jobs/{job['id']}/results")
    assert result_resp.status_code == 200
    result = result_resp.json()
    assert result["job_id"] == job["id"]
    assert isinstance(result["images"], list)
    assert "copy" in result


def test_generation_partial_success_and_cancel(client):
    setup_model_routing(client)
    session = create_session(client)

    asset_resp = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "food_name",
            "content": "芝士焗龙虾",
        },
    )
    assert asset_resp.status_code == 201

    partial_style = create_style(
        client,
        session["id"],
        {"force_partial_fail": True},
    )
    partial_job = create_generation_job(client, session["id"], partial_style["id"], image_count=3)
    partial_end = wait_for_job_end(client, partial_job["id"])
    assert partial_end["status"] == "partial_success"
    assert partial_end["error_code"] == "E-1004"

    cancel_style = create_style(client, session["id"], {"painting_style": ["写实"]})
    cancel_job = create_generation_job(client, session["id"], cancel_style["id"], image_count=5)
    cancel_resp = client.post(f"/api/v1/jobs/{cancel_job['id']}/cancel")
    assert cancel_resp.status_code == 202
    canceled = wait_for_job_end(client, cancel_job["id"])
    assert canceled["status"] == "canceled"


def test_not_found_error_code_for_style(client):
    response = client.delete("/api/v1/styles/missing-style")
    assert response.status_code == 404
    assert response.json()["code"] == "E-2003"


def test_style_profile_rejects_non_image_sample_asset(client):
    session = create_session(client, title="样例图校验会话")
    text_asset_response = client.post(
        "/api/v1/assets/text",
        json={
            "session_id": session["id"],
            "asset_type": "text",
            "content": "这是一段文本素材",
        },
    )
    assert text_asset_response.status_code == 201
    text_asset_id = text_asset_response.json()["id"]

    create_response = client.post(
        "/api/v1/styles",
        json={
            "session_id": session["id"],
            "name": "样例图非法风格",
            "style_payload": {
                "painting_style": "手绘水彩",
                "color_mood": "温暖治愈",
                "prompt_example": "请保持复古旅行手账风格。",
                "style_prompt": "请保持复古旅行手账风格。",
                "sample_image_asset_id": text_asset_id,
                "extra_keywords": [],
            },
        },
    )
    assert create_response.status_code == 400
    assert create_response.json()["message"] == "样例图必须绑定有效的图片素材"


def test_style_profile_accepts_image_sample_asset_and_returns_preview_url(client):
    session = create_session(client, title="样例图成功会话")
    image_asset = _insert_image_asset(client, session["id"])

    create_response = client.post(
        "/api/v1/styles",
        json={
            "session_id": session["id"],
            "name": "样例图有效风格",
            "style_payload": {
                "painting_style": "手绘水彩",
                "color_mood": "温暖治愈",
                "prompt_example": "请保持复古旅行手账风格。",
                "style_prompt": "请保持复古旅行手账风格。",
                "sample_image_asset_id": image_asset["id"],
                "extra_keywords": [],
            },
        },
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["style_payload"]["sample_image_asset_id"] == image_asset["id"]
    assert body["sample_image_preview_url"].startswith("http://127.0.0.1:8887/static/images/")


def test_style_profile_update_rejects_cross_session_sample_asset(client):
    session_a = create_session(client, title="样例图会话A")
    session_b = create_session(client, title="样例图会话B")
    style = create_style(client, session_a["id"], {"painting_style": "电影写实"})
    foreign_image = _insert_image_asset(client, session_b["id"], "foreign-style-sample.png")

    update_response = client.patch(
        f"/api/v1/styles/{style['id']}",
        json={
            "style_payload": {
                "painting_style": "电影写实",
                "color_mood": "暖金氛围",
                "prompt_example": "保持统一电影感。",
                "style_prompt": "保持统一电影感。",
                "sample_image_asset_id": foreign_image["id"],
                "extra_keywords": [],
            }
        },
    )
    assert update_response.status_code == 400
    assert update_response.json()["message"] == "样例图必须属于当前会话"

