from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib import parse as url_parse

from backend.app.services.style.errors import StyleFallbackError

logger = logging.getLogger(__name__)


class StyleModelCallerMixin:
    def _call_text_model(
        self,
        provider: dict[str, Any],
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        *,
        strict_json: bool,
    ) -> str:
        provider_id = str(provider.get("id") or "").strip()
        model_candidates = self._build_model_name_candidates(model_name)
        last_error: StyleFallbackError | None = None
        for model_candidate in model_candidates:
            protocol_order = self._build_protocol_order(provider_id, provider.get("api_protocol"))
            for index, protocol in enumerate(protocol_order):
                endpoint, request_body = self._build_protocol_request(
                    provider=provider,
                    protocol=protocol,
                    model_name=model_candidate,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    strict_json=strict_json,
                )
                try:
                    response_payload = self._post_json(endpoint, request_body, provider["api_key"])
                    text = self._extract_text_content(response_payload, protocol)
                    if not text.strip():
                        raise StyleFallbackError("upstream_empty_text", "上游未返回有效文本")
                    if provider_id:
                        self._provider_protocol_overrides[provider_id] = protocol
                    return text
                except StyleFallbackError as exc:
                    last_error = exc
                    if index == 0 and self._should_retry_protocol(exc):
                        logger.warning(
                            "风格对话协议回退重试: from=%s to=%s reason=%s detail=%s",
                            protocol,
                            protocol_order[1],
                            exc.reason,
                            exc.detail,
                        )
                        continue
                    break
            if self._should_retry_model_name(last_error) and model_candidate != model_candidates[-1]:
                next_candidate = model_candidates[model_candidates.index(model_candidate) + 1]
                logger.warning(
                    "风格对话模型名降级重试: from=%s to=%s reason=%s detail=%s",
                    model_candidate,
                    next_candidate,
                    last_error.reason if last_error else "",
                    last_error.detail if last_error else "",
                )
                continue
            break
        if last_error:
            raise StyleFallbackError("protocol_both_failed", f"双协议请求失败: {last_error.reason} | {last_error.detail}")
        raise StyleFallbackError("protocol_both_failed", "双协议请求失败")

    def _call_text_model_with_images(
        self,
        provider: dict[str, Any],
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        image_urls: list[str],
        *,
        strict_json: bool,
    ) -> str:
        normalized_urls = self._normalize_multimodal_image_inputs(image_urls)
        if not normalized_urls:
            return self._call_text_model(
                provider,
                model_name,
                system_prompt,
                user_prompt,
                strict_json=strict_json,
            )
        provider_id = str(provider.get("id") or "").strip()
        model_candidates = self._build_model_name_candidates(model_name)
        last_error: StyleFallbackError | None = None
        for model_candidate in model_candidates:
            protocol_order = self._build_protocol_order(provider_id, provider.get("api_protocol"))
            for index, protocol in enumerate(protocol_order):
                endpoint, request_body = self._build_multimodal_protocol_request(
                    provider=provider,
                    protocol=protocol,
                    model_name=model_candidate,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    image_urls=normalized_urls,
                    strict_json=strict_json,
                )
                try:
                    response_payload = self._post_json(endpoint, request_body, provider["api_key"])
                    text = self._extract_text_content(response_payload, protocol)
                    if not text.strip():
                        raise StyleFallbackError("upstream_empty_text", "上游未返回有效文本")
                    if provider_id:
                        self._provider_protocol_overrides[provider_id] = protocol
                    return text
                except StyleFallbackError as exc:
                    last_error = exc
                    if index == 0 and self._should_retry_protocol(exc):
                        logger.warning(
                            "多模态协议回退重试: from=%s to=%s reason=%s detail=%s",
                            protocol,
                            protocol_order[1],
                            exc.reason,
                            exc.detail,
                        )
                        continue
                    break
            if self._should_retry_model_name(last_error) and model_candidate != model_candidates[-1]:
                next_candidate = model_candidates[model_candidates.index(model_candidate) + 1]
                logger.warning(
                    "多模态模型名降级重试: from=%s to=%s reason=%s detail=%s",
                    model_candidate,
                    next_candidate,
                    last_error.reason if last_error else "",
                    last_error.detail if last_error else "",
                )
                continue
            break
        if last_error:
            raise StyleFallbackError("protocol_both_failed", f"双协议请求失败: {last_error.reason} | {last_error.detail}")
        raise StyleFallbackError("protocol_both_failed", "双协议请求失败")

    def _normalize_multimodal_image_inputs(self, image_urls: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in image_urls:
            if not isinstance(value, str):
                continue
            raw = value.strip()
            if not raw:
                continue
            encoded = self._to_data_url_if_local(raw)
            normalized.append(encoded or raw)
            if len(normalized) >= 4:
                break
        return normalized

    def _build_model_name_candidates(self, model_name: str) -> list[str]:
        primary = str(model_name or "").strip()
        if not primary:
            return [primary]
        candidates = [primary]
        lowered = primary.lower()
        if "thinking" not in lowered:
            return candidates
        fallback = re.sub(r"[-_]?thinking[-_a-z0-9]*$", "", primary, flags=re.IGNORECASE).strip("-_ ")
        if fallback and fallback not in candidates:
            candidates.append(fallback)
        return candidates

    def _should_retry_model_name(self, error: StyleFallbackError | None) -> bool:
        if not error:
            return False
        detail = str(error.detail or "").lower()
        if error.reason == "upstream_http_error":
            return "thinking budget" in detail and "thinking level" in detail
        if error.reason == "upstream_timeout_or_network":
            return True
        if error.reason == "protocol_both_failed" and "upstream_timeout_or_network" in detail:
            return True
        return "remote end closed connection without response" in detail

    def _to_data_url_if_local(self, source: str) -> str | None:
        local_path = self._resolve_local_image_path(source)
        if not local_path:
            return None
        try:
            content = local_path.read_bytes()
        except OSError:
            return None
        mime_type = self._guess_image_mime(local_path, content)
        if not mime_type:
            return None
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _resolve_local_image_path(self, source: str) -> Path | None:
        if not self.storage:
            return None
        candidate = Path(source)
        if candidate.is_file():
            return candidate
        normalized = source.replace("\\", "/").strip()
        if normalized.startswith("http://") or normalized.startswith("https://"):
            parsed = url_parse.urlparse(normalized)
            normalized = parsed.path or ""
        static_prefix = "/static/"
        if normalized.startswith(static_prefix):
            relative = normalized[len(static_prefix) :].lstrip("/")
            path = self.storage.base_dir / relative
            return path if path.is_file() else None
        if self.public_base_url:
            base = self.public_base_url.rstrip("/")
            prefixed = f"{base}/static/"
            if source.startswith(prefixed):
                relative = source[len(prefixed) :].lstrip("/")
                path = self.storage.base_dir / relative
                return path if path.is_file() else None
        return None

    def _guess_image_mime(self, path: Path, content: bytes) -> str | None:
        ext = path.suffix.lower().lstrip(".")
        mapping = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
            "gif": "image/gif",
            "avif": "image/avif",
        }
        if ext in mapping:
            return mapping[ext]
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith(b"RIFF") and b"WEBP" in content[:16]:
            return "image/webp"
        if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
            return "image/gif"
        return None

    def _should_retry_strict_json(self, error: StyleFallbackError) -> bool:
        return error.reason in {
            "json_missing_object",
            "json_parse_failed",
            "json_structure_invalid",
            "options_missing",
            "options_title_invalid",
            "options_items_invalid",
            "options_items_empty",
            "options_max_invalid",
            "options_max_out_of_range",
        }

    def _extract_text_content(self, payload: dict[str, Any], protocol: str) -> str:
        if protocol == "responses":
            response_text = self._extract_responses_text(payload)
            if response_text:
                return response_text
            candidate_text = self._extract_candidate_text(payload)
            if candidate_text:
                return candidate_text
            raise StyleFallbackError("responses_missing_text", "responses 协议未返回文本内容")
        chat_text = self._extract_chat_text(payload)
        if chat_text:
            return chat_text
        candidate_text = self._extract_candidate_text(payload)
        if candidate_text:
            return candidate_text
        raise StyleFallbackError("chat_missing_text", "chat_completions 协议未返回文本内容")

    def _extract_responses_text(self, payload: dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        output_blocks = payload.get("output")
        if not isinstance(output_blocks, list):
            return ""
        for block in output_blocks:
            if not isinstance(block, dict):
                continue
            content = block.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
                output_part_text = part.get("output_text")
                if isinstance(output_part_text, str) and output_part_text.strip():
                    return output_part_text.strip()
        return ""

    def _extract_chat_text(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        first_message = first_choice.get("message") if isinstance(first_choice, dict) else {}
        if not isinstance(first_message, dict):
            return ""
        content = first_message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if not isinstance(content, list):
            return ""
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
        return ""

    def _extract_candidate_text(self, payload: dict[str, Any]) -> str:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            return ""
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content")
            if not isinstance(content, dict):
                continue
            parts = content.get("parts")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
        return ""

    def _parse_model_payload(self, model_text: str) -> dict[str, Any]:
        text = model_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        start_index = text.find("{")
        end_index = text.rfind("}")
        if start_index == -1 or end_index == -1 or end_index < start_index:
            raise StyleFallbackError("json_missing_object", "模型输出缺少 JSON 对象")
        json_text = text[start_index : end_index + 1]
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise StyleFallbackError("json_parse_failed", "模型输出 JSON 解析失败") from exc
        if not isinstance(payload, dict):
            raise StyleFallbackError("json_structure_invalid", "模型输出 JSON 结构非法")
        return payload

    def _extract_and_validate_options(self, payload: dict[str, Any]) -> dict[str, Any]:
        options = payload.get("options")
        if not isinstance(options, dict):
            raise StyleFallbackError("options_missing", "模型输出缺少 options")
        title = options.get("title")
        items = options.get("items")
        max_value = options.get("max")
        if not isinstance(title, str) or not title.strip():
            raise StyleFallbackError("options_title_invalid", "options.title 缺失或非法")
        if not isinstance(items, list):
            raise StyleFallbackError("options_items_invalid", "options.items 缺失或非法")
        normalized_items = self._normalize_items(items)
        if not normalized_items:
            raise StyleFallbackError("options_items_empty", "options.items 不能为空")
        if not isinstance(max_value, int):
            raise StyleFallbackError("options_max_invalid", "options.max 必须为整数")
        if max_value < 1 or max_value > len(normalized_items):
            raise StyleFallbackError("options_max_out_of_range", "options.max 超出合法范围")
        return {"title": title.strip(), "items": normalized_items, "max": max_value}

    def _normalize_items(self, items: list[Any]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in items:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized

    def build_user_facing_upstream_message(self, error: StyleFallbackError, *, include_detail: bool = True) -> str:
        reason = str(error.reason or "").strip()
        detail = self._normalize_upstream_error_detail(error.detail)
        lowered_detail = detail.lower()
        if reason in {"routing_unavailable", "text_provider_missing", "text_model_missing", "text_provider_unavailable"}:
            return "模型服务连接失败，模型配置不可用，请先检查模型设置"
        if reason in {"protocol_endpoint_not_supported", "protocol_incompatible"}:
            base = "模型服务连接失败，模型协议不兼容，请切换协议或模型后重试"
        elif reason == "upstream_timeout_or_network":
            base = "模型服务连接失败，模型服务连接超时，请稍后重试"
        elif reason in {
            "upstream_invalid_json",
            "upstream_invalid_payload",
            "responses_missing_text",
            "chat_missing_text",
            "upstream_empty_text",
            "json_missing_object",
            "json_parse_failed",
            "json_structure_invalid",
            "options_missing",
            "options_title_invalid",
            "options_items_invalid",
            "options_items_empty",
            "options_max_invalid",
            "options_max_out_of_range",
        }:
            base = "模型输出格式异常，请重试"
        elif reason == "upstream_http_error":
            base = self._classify_upstream_http_error_message(lowered_detail)
        elif reason == "protocol_both_failed":
            base = "模型服务连接失败，请稍后重试"
        else:
            base = "模型服务连接失败，请稍后重试"
        if include_detail and detail:
            return f"{base}（上游：{detail}）"
        return base

    def _classify_upstream_http_error_message(self, lowered_detail: str) -> str:
        if any(marker in lowered_detail for marker in ("429", "rate limit", "too many requests", "限流")):
            return "模型服务触发限流，请稍后重试"
        if any(marker in lowered_detail for marker in ("401", "unauthorized", "api key", "invalid key", "鉴权")):
            return "模型服务鉴权失败，请检查密钥配置"
        if any(marker in lowered_detail for marker in ("403", "forbidden", "permission", "权限")):
            return "模型服务权限不足，请检查模型权限"
        if any(marker in lowered_detail for marker in ("402", "payment", "insufficient", "quota", "credit", "额度", "余额", "预扣费")):
            return "模型服务额度不足，请充值后重试"
        if any(marker in lowered_detail for marker in ("404", "not found", "model not found", "invalid model", "模型不存在")):
            return "模型不可用，请切换模型后重试"
        if any(marker in lowered_detail for marker in ("500", "502", "503", "504", "gateway", "upstream")):
            return "模型服务暂时不可用，请稍后重试"
        return "模型服务请求失败，请重试"

    def _normalize_upstream_error_detail(self, detail: str | None) -> str:
        if not isinstance(detail, str):
            return ""
        text = " ".join(detail.strip().split())
        if not text:
            return ""
        if len(text) > 220:
            return f"{text[:220]}..."
        return text
