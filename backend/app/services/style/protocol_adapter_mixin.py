from __future__ import annotations

from typing import Any

from backend.app.infra.http_client import (
    HttpClientHttpError,
    HttpClientInvalidJsonError,
    HttpClientInvalidPayloadError,
    HttpClientNetworkError,
    post_json,
)
from backend.app.services.style.errors import StyleFallbackError


class StyleProtocolAdapterMixin:
    def _build_protocol_order(self, provider_id: str, protocol: str | None) -> list[str]:
        override = self._provider_protocol_overrides.get(provider_id)
        active_protocol = override or protocol
        if active_protocol == "chat_completions":
            return ["chat_completions", "responses"]
        return ["responses", "chat_completions"]

    def _build_protocol_request(
        self,
        *,
        provider: dict[str, Any],
        protocol: str,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        strict_json: bool,
    ) -> tuple[str, dict[str, Any]]:
        base_url = provider["base_url"].rstrip("/")
        if protocol == "responses":
            payload = {
                "model": model_name,
                "instructions": system_prompt,
                "input": [{"role": "user", "content": [{"type": "input_text", "text": user_prompt}]}],
            }
            if strict_json:
                payload["temperature"] = 0
            return f"{base_url}/responses", payload
        return (
            f"{base_url}/chat/completions",
            {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0 if strict_json else 0.7,
            },
        )

    def _build_multimodal_protocol_request(
        self,
        *,
        provider: dict[str, Any],
        protocol: str,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        image_urls: list[str],
        strict_json: bool,
    ) -> tuple[str, dict[str, Any]]:
        base_url = provider["base_url"].rstrip("/")
        if protocol == "responses":
            content: list[dict[str, Any]] = [{"type": "input_text", "text": user_prompt}]
            content.extend({"type": "input_image", "image_url": image_url} for image_url in image_urls)
            payload: dict[str, Any] = {
                "model": model_name,
                "instructions": system_prompt,
                "input": [{"role": "user", "content": content}],
            }
            if strict_json:
                payload["temperature"] = 0
            return f"{base_url}/responses", payload
        content_blocks: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        content_blocks.extend({"type": "image_url", "image_url": {"url": image_url}} for image_url in image_urls)
        return (
            f"{base_url}/chat/completions",
            {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content_blocks},
                ],
                "temperature": 0 if strict_json else 0.7,
            },
        )

    def _should_retry_protocol(self, error: StyleFallbackError) -> bool:
        return error.reason in {"protocol_endpoint_not_supported", "protocol_incompatible"}

    def _looks_like_protocol_incompatible(self, status_code: int, body_text: str) -> bool:
        lowered = body_text.lower()
        common_markers = [
            "unsupported",
            "not support",
            "invalid_request_error",
            "/responses",
            "/chat/completions",
            "messages",
            "input",
            "instructions",
        ]
        if status_code == 400:
            return any(marker in lowered for marker in common_markers)
        server_side_markers = [
            "not implemented",
            "convert_request_failed",
            "request conversion failed",
            "new_api_error",
        ]
        if status_code >= 500:
            return any(marker in lowered for marker in (common_markers + server_side_markers))
        return False

    def _post_json(self, url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        try:
            return post_json(url, payload, api_key, timeout=35)
        except HttpClientHttpError as exc:
            if exc.status_code in {404, 405}:
                raise StyleFallbackError("protocol_endpoint_not_supported", f"HTTP {exc.status_code}") from exc
            if self._looks_like_protocol_incompatible(exc.status_code, exc.body_text):
                raise StyleFallbackError("protocol_incompatible", f"HTTP {exc.status_code}: {exc.body_text[:120]}") from exc
            raise StyleFallbackError("upstream_http_error", f"HTTP {exc.status_code}: {exc.body_text[:120]}") from exc
        except HttpClientNetworkError as exc:
            raise StyleFallbackError("upstream_timeout_or_network", exc.detail) from exc
        except HttpClientInvalidJsonError as exc:
            raise StyleFallbackError("upstream_invalid_json", "上游响应不是有效 JSON") from exc
        except HttpClientInvalidPayloadError as exc:
            raise StyleFallbackError("upstream_invalid_payload", f"上游响应结构非法: {type(exc.payload).__name__}") from exc
