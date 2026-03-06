from __future__ import annotations

from typing import Any

from backend.app.core.errors import DomainError
from backend.app.infra.http_client import (
    HttpClientHttpError,
    HttpClientInvalidJsonError,
    HttpClientInvalidPayloadError,
    HttpClientNetworkError,
    post_json,
)


class GenerationUpstreamErrorMixin:
    def _post_json(
        self,
        *,
        provider_id: str,
        model_name: str,
        url: str,
        api_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            parsed = post_json(url, payload, api_key, timeout=45)
        except HttpClientHttpError as error:
            upstream_error_message = self._extract_error_message_from_raw_text(error.body_text)
            self._log_upstream_failure(
                provider_id=provider_id,
                model_name=model_name,
                endpoint=url,
                http_status=error.status_code,
                reason="http_error",
            )
            if upstream_error_message:
                raise DomainError(
                    code="E-1004",
                    message=f"图片生成失败：{self._format_upstream_provider_error(upstream_error_message)}",
                    status_code=400,
                ) from error
            raise DomainError(
                code="E-1004",
                message=f"图片生成失败：{self._format_upstream_provider_error(f'HTTP {error.status_code}')}",
                status_code=400,
            ) from error
        except HttpClientNetworkError as error:
            self._log_upstream_failure(
                provider_id=provider_id,
                model_name=model_name,
                endpoint=url,
                http_status=None,
                reason="network_error",
            )
            normalized_error = self._normalize_upstream_error_text(error.detail)
            detail = normalized_error or "network"
            raise DomainError(
                code="E-1004",
                message=f"图片生成失败：上游网络异常（上游：{detail}）",
                status_code=400,
            ) from error
        except HttpClientInvalidJsonError as error:
            self._log_upstream_failure(
                provider_id=provider_id,
                model_name=model_name,
                endpoint=url,
                http_status=200,
                reason="invalid_json",
            )
            raw_preview = self._normalize_upstream_error_text(error.body_text)
            suffix = f"（上游：{raw_preview}）" if raw_preview else ""
            raise DomainError(code="E-1004", message=f"图片生成失败：上游响应格式错误{suffix}", status_code=400) from error
        except HttpClientInvalidPayloadError as error:
            self._log_upstream_failure(
                provider_id=provider_id,
                model_name=model_name,
                endpoint=url,
                http_status=200,
                reason="invalid_payload_type",
            )
            raise DomainError(code="E-1004", message="图片生成失败：上游响应格式错误", status_code=400) from error

        if self._is_explicit_upstream_error_payload(parsed):
            upstream_error_message = self._extract_upstream_error_message(parsed)
            self._log_upstream_failure(
                provider_id=provider_id,
                model_name=model_name,
                endpoint=url,
                http_status=200,
                reason="upstream_error_payload",
            )
            raise DomainError(
                code="E-1004",
                message=f"图片生成失败：{self._format_upstream_provider_error(upstream_error_message or '上游服务返回错误')}",
                status_code=400,
            )
        return parsed

    def _extract_upstream_error_message(self, payload: dict[str, Any]) -> str | None:
        direct_fields = [payload.get("message"), payload.get("detail"), payload.get("error_message")]
        for field in direct_fields:
            if isinstance(field, str) and field.strip():
                return field.strip()
        error_field = payload.get("error")
        if isinstance(error_field, str) and error_field.strip():
            return error_field.strip()
        if isinstance(error_field, dict):
            for key in ("message", "detail", "error", "reason", "type"):
                value = error_field.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        code = payload.get("code")
        if isinstance(code, str) and code.strip():
            return code.strip()
        return None

    def _has_image_candidate(self, payload: dict[str, Any]) -> bool:
        data_items = payload.get("data")
        if isinstance(data_items, list) and data_items:
            first_item = data_items[0]
            if isinstance(first_item, dict):
                for key in ("b64_json", "image_base64", "base64", "url", "image_url", "download_url", "image"):
                    value = first_item.get(key)
                    if isinstance(value, str) and value.strip():
                        return True
            if isinstance(first_item, str) and first_item.strip():
                return True
        for key in ("b64_json", "image_base64", "base64", "url", "image_url", "download_url", "image", "output"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return True
        return False

    def _is_explicit_upstream_error_payload(self, payload: dict[str, Any]) -> bool:
        if isinstance(payload.get("error"), (str, dict)):
            return True
        if payload.get("success") is False:
            return True
        status = payload.get("status")
        if isinstance(status, str) and status.lower() in {"error", "failed", "fail"}:
            return True
        code = payload.get("code")
        if isinstance(code, str):
            lowered_code = code.lower()
            if any(marker in lowered_code for marker in ("error", "fail", "invalid")):
                return True
        if self._has_image_candidate(payload):
            return False
        for key in ("message", "detail", "error_message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return True
        return False

    def _extract_error_message_from_raw_text(self, raw_text: str) -> str | None:
        stripped = (raw_text or "").strip()
        if not stripped:
            return None
        if self._looks_like_html_error_page(stripped):
            return "上游网关拒绝访问（返回 HTML 页面）"
        try:
            import json

            parsed = json.loads(stripped)
        except Exception:
            return stripped[:120]
        if not isinstance(parsed, dict):
            return stripped[:120]
        return self._extract_upstream_error_message(parsed)

    def _format_upstream_provider_error(self, raw_text: str) -> str:
        normalized = self._normalize_upstream_error_text(raw_text)
        lowered = normalized.lower()
        if any(marker in lowered for marker in ("429", "rate limit", "too many requests", "限流")):
            base = "上游服务触发限流，请稍后重试"
        elif any(marker in lowered for marker in ("401", "unauthorized", "api key", "invalid key", "鉴权")):
            base = "上游鉴权失败，请检查密钥配置"
        elif any(marker in lowered for marker in ("403", "forbidden", "permission", "权限")):
            base = "上游权限不足，请检查模型权限"
        elif any(marker in lowered for marker in ("402", "payment", "insufficient", "quota", "credit", "额度", "余额", "预扣费")):
            base = "上游额度不足，请充值后重试"
        elif any(marker in lowered for marker in ("not implemented", "protocol", "unsupported", "不支持")):
            base = "上游协议不兼容，请切换协议或模型"
        elif any(marker in lowered for marker in ("timeout", "timed out", "network", "连接", "超时")):
            base = "上游网络超时，请稍后重试"
        elif any(marker in lowered for marker in ("model not found", "invalid model", "模型不存在")):
            base = "上游模型不可用，请切换模型"
        elif any(marker in lowered for marker in ("size", "pixels", "分辨率", "尺寸")):
            base = "上游参数不符合要求，请调整后重试"
        elif any(marker in lowered for marker in ("500", "502", "503", "504", "gateway", "upstream")):
            base = "上游服务暂不可用，请稍后重试"
        else:
            base = "上游服务返回错误"
        if normalized:
            return f"{base}（上游：{normalized}）"
        return base

    def _normalize_upstream_error_text(self, raw_text: str, max_len: int = 220) -> str:
        if not isinstance(raw_text, str):
            return ""
        normalized = " ".join(raw_text.strip().split())
        if not normalized:
            return ""
        if len(normalized) > max_len:
            return f"{normalized[:max_len]}..."
        return normalized

    def _looks_like_html_error_page(self, body_text: str) -> bool:
        lowered = (body_text or "").strip().lower()
        return lowered.startswith("<!doctype html") or lowered.startswith("<html")

    def _build_upstream_http_error_detail(self, *, status_code: int, body_text: str) -> str:
        if self._looks_like_html_error_page(body_text):
            return f"上游网关拒绝访问（HTTP {status_code}，返回 HTML 页面）"
        normalized_raw = self._extract_error_message_from_raw_text(body_text) or f"HTTP {status_code}"
        return f"{self._format_upstream_provider_error(normalized_raw)}（HTTP {status_code}）"

    def _log_upstream_failure(
        self,
        *,
        provider_id: str,
        model_name: str,
        endpoint: str,
        http_status: int | None,
        reason: str,
    ) -> None:
        self._logger.warning(
            "生图上游失败 provider_id=%s model_name=%s endpoint=%s http_status=%s reason=%s",
            provider_id,
            model_name,
            endpoint,
            http_status if http_status is not None else "-",
            reason,
        )
