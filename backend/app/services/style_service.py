from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib import error as url_error
from urllib import parse as url_parse
from urllib import request

from backend.app.core.errors import DomainError, not_found
from backend.app.core.utils import new_id, now_iso
from backend.app.domain.enums import STYLE_STAGE_ORDER
from backend.app.infra.storage import Storage
from backend.app.repositories.asset_repo import AssetRepository
from backend.app.repositories.session_repo import SessionRepository
from backend.app.repositories.style_repo import StyleRepository
from backend.app.services.model_service import ModelService
from backend.app.services.style.constants import STAGE_OPTION_RULES
from backend.app.services.style.payload_mixin import StylePayloadMixin

logger = logging.getLogger(__name__)


class StyleFallbackError(Exception):
    def __init__(self, reason: str, detail: str | None = None):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


class StyleService(StylePayloadMixin):
    def __init__(
        self,
        style_repo: StyleRepository,
        session_repo: SessionRepository,
        model_service: ModelService,
        asset_repo: AssetRepository | None = None,
        storage: Storage | None = None,
        public_base_url: str | None = None,
    ):
        self.style_repo = style_repo
        self.session_repo = session_repo
        self.model_service = model_service
        self.asset_repo = asset_repo
        self.storage = storage
        self.public_base_url = (public_base_url or "").rstrip("/")
        # 记录每个提供商最近一次成功协议，避免每次都先走不兼容端点。
        self._provider_protocol_overrides: dict[str, str] = {}

    def chat(
        self,
        *,
        session_id: str,
        stage: str,
        user_reply: str,
        selected_items: list[str] | None,
    ) -> dict[str, Any]:
        session = self.session_repo.get(session_id)
        if not session:
            raise not_found("会话", session_id)

        normalized_stage = self._normalize_stage(stage)
        picked_items = selected_items or []
        response_stage = self._resolve_response_stage(normalized_stage, picked_items)

        fallback_used = False
        try:
            reply, options = self._generate_dynamic_response(
                session=session,
                stage=response_stage,
                user_reply=user_reply,
                selected_items=picked_items,
            )
        except StyleFallbackError as exc:
            logger.warning(
                "风格对话失败: session_id=%s stage=%s reason=%s detail=%s",
                session_id,
                response_stage,
                exc.reason,
                exc.detail,
            )
            raise DomainError(
                code="E-1004",
                message=self.build_user_facing_upstream_message(exc),
                status_code=503,
                details={"reason": exc.reason},
            ) from exc
        except Exception as exc:
            logger.exception("风格对话发生未预期异常")
            raise DomainError(code="E-1099", message="系统内部错误", status_code=500) from exc

        current_index = STYLE_STAGE_ORDER.index(response_stage)
        is_finished = current_index == len(STYLE_STAGE_ORDER) - 1
        next_stage = "" if is_finished else STYLE_STAGE_ORDER[current_index + 1]
        return {
            "reply": reply,
            "options": options,
            "stage": response_stage,
            "next_stage": next_stage,
            "is_finished": is_finished,
            "fallback_used": fallback_used,
        }

    def create(self, session_id: str | None, name: str, style_payload: dict[str, Any]) -> dict[str, Any]:
        if session_id and not self.session_repo.get(session_id):
            raise not_found("会话", session_id)
        normalized_payload = self._normalize_style_payload(style_payload)
        self._validate_sample_image_assets(session_id=session_id, style_payload=normalized_payload, strict=True)
        self._sync_sample_image_snapshot(normalized_payload, previous_payload=None)
        now = now_iso()
        profile = {
            "id": new_id(),
            "session_id": session_id,
            "name": name,
            "style_payload": normalized_payload,
            "is_builtin": False,
            "created_at": now,
            "updated_at": now,
        }
        created = self.style_repo.create(profile)
        return self._enrich_profile(created)

    def list_all(self) -> list[dict[str, Any]]:
        return [self._enrich_profile(profile) for profile in self.style_repo.list_all()]

    def update(self, style_id: str, name: str | None, style_payload: dict[str, Any] | None) -> dict[str, Any]:
        profile = self.style_repo.get(style_id)
        if not profile:
            raise not_found("风格", style_id)
        now = now_iso()
        changed = False
        if name is not None:
            profile = self.style_repo.update_name(style_id, name, now) or profile
            changed = True
        if style_payload is not None:
            previous_payload = self._normalize_style_payload(dict(profile.get("style_payload") or {}))
            normalized_payload = self._normalize_style_payload(style_payload)
            self._validate_sample_image_assets(
                session_id=profile.get("session_id"),
                style_payload=normalized_payload,
                strict=True,
            )
            self._sync_sample_image_snapshot(normalized_payload, previous_payload=previous_payload)
            profile = self.style_repo.update_payload(style_id, normalized_payload, now) or profile
            changed = True
        if not changed:
            raise DomainError(code="E-1099", message="未提供可更新字段", status_code=400)
        return self._enrich_profile(profile)

    def delete(self, style_id: str) -> bool:
        deleted = self.style_repo.delete(style_id)
        if not deleted:
            raise not_found("风格", style_id)
        return True

    def _normalize_stage(self, stage: str) -> str:
        if stage == "init":
            return "painting_style"
        if stage not in STYLE_STAGE_ORDER:
            raise DomainError(code="E-1099", message="风格阶段不合法", status_code=400)
        return stage

    def _resolve_response_stage(self, stage: str, selected_items: list[str]) -> str:
        if not selected_items:
            return stage
        stage_max = STAGE_OPTION_RULES[stage]["max"]
        current_index = STYLE_STAGE_ORDER.index(stage)
        if stage_max > 1 and current_index < len(STYLE_STAGE_ORDER) - 1:
            return STYLE_STAGE_ORDER[current_index + 1]
        return stage

    def _generate_dynamic_response(
        self,
        *,
        session: dict[str, Any],
        stage: str,
        user_reply: str,
        selected_items: list[str],
    ) -> tuple[str, dict[str, Any]]:
        provider, model_name = self._resolve_text_model_provider()
        user_prompt = self._build_user_prompt(session, stage, user_reply, selected_items)
        system_prompt = self._build_system_prompt(stage, strict_json=False)
        session_image_urls = self._collect_session_image_urls(str(session.get("id") or ""))
        if session_image_urls:
            model_text = self._call_text_model_with_images(
                provider,
                model_name,
                system_prompt,
                user_prompt,
                session_image_urls,
                strict_json=False,
            )
        else:
            model_text = self._call_text_model(
                provider,
                model_name,
                system_prompt,
                user_prompt,
                strict_json=False,
            )
        payload = None
        options = None
        try:
            payload = self._parse_model_payload(model_text)
            options = self._extract_and_validate_options(payload)
        except StyleFallbackError as exc:
            if not self._should_retry_strict_json(exc):
                raise
            logger.warning("风格对话触发严格 JSON 重试: reason=%s detail=%s", exc.reason, exc.detail)
            strict_system_prompt = self._build_system_prompt(stage, strict_json=True)
            if session_image_urls:
                retry_text = self._call_text_model_with_images(
                    provider,
                    model_name,
                    strict_system_prompt,
                    user_prompt,
                    session_image_urls,
                    strict_json=True,
                )
            else:
                retry_text = self._call_text_model(
                    provider,
                    model_name,
                    strict_system_prompt,
                    user_prompt,
                    strict_json=True,
                )
            payload = self._parse_model_payload(retry_text)
            options = self._extract_and_validate_options(payload)

        reply = payload.get("reply") if isinstance(payload, dict) else None
        if not isinstance(reply, str) or not reply.strip():
            raise StyleFallbackError("reply_missing", "模型输出缺少 reply")
        return reply, options

    def _collect_session_image_urls(self, session_id: str) -> list[str]:
        if not session_id or not self.asset_repo:
            return []
        urls: list[str] = []
        seen: set[str] = set()
        for asset in self.asset_repo.list_by_session(session_id):
            if asset.get("asset_type") != "image":
                continue
            public_url = self._build_public_image_url(asset.get("file_path"))
            if not public_url or public_url in seen:
                continue
            seen.add(public_url)
            urls.append(public_url)
            if len(urls) >= 4:
                break
        return urls

    def _resolve_text_model_provider(self) -> tuple[dict[str, Any], str]:
        try:
            routing = self.model_service.require_routing()
        except DomainError as exc:
            if exc.code == "E-1006":
                raise StyleFallbackError("routing_unavailable", "模型路由不可用") from exc
            raise
        text_model = routing.get("text_model") or {}
        provider_id = text_model.get("provider_id")
        model_name = text_model.get("model_name")
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise StyleFallbackError("text_provider_missing", "文字模型提供商未配置")
        if not isinstance(model_name, str) or not model_name.strip():
            raise StyleFallbackError("text_model_missing", "文字模型未配置")

        provider = self.model_service.provider_repo.get(provider_id)
        if not provider or not provider.get("enabled"):
            raise StyleFallbackError("text_provider_unavailable", "文字模型提供商不可用")
        return provider, model_name

    def _build_system_prompt(self, stage: str, *, strict_json: bool) -> str:
        stage_rule = STAGE_OPTION_RULES[stage]
        prompt = (
            "你是 Savory Canvas 风格对话助手。"
            "必须返回严格 JSON，不要输出 Markdown。"
            "JSON 结构固定为："
            "{\"reply\":\"中文回复\",\"options\":{\"title\":\"中文标题\",\"items\":[\"选项1\"],\"max\":1}}。"
            f"当前阶段：{stage}。"
            f"options.title 必须为“{stage_rule['title']}”。"
            f"options.max 必须是整数，且 1 <= max <= {stage_rule['max']}。"
            "options.items 必须是非空中文字符串数组。"
        )
        if strict_json:
            prompt += "本次重试必须只输出一个 JSON 对象，temperature=0，不允许解释文本。"
        return prompt

    def _build_user_prompt(
        self,
        session: dict[str, Any],
        stage: str,
        user_reply: str,
        selected_items: list[str],
    ) -> str:
        selected_text = "、".join(selected_items) if selected_items else "无"
        return (
            f"会话标题：{session.get('title', '')}\n"
            f"当前阶段：{stage}\n"
            f"用户输入：{user_reply or '无'}\n"
            f"已选项：{selected_text}\n"
            "请返回可直接用于前端渲染的 JSON。"
        )

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
            raise StyleFallbackError("protocol_both_failed", f"双协议调用失败: {last_error.reason} | {last_error.detail}")
        raise StyleFallbackError("protocol_both_failed", "双协议调用失败")

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
            raise StyleFallbackError("protocol_both_failed", f"双协议调用失败: {last_error.reason} | {last_error.detail}")
        raise StyleFallbackError("protocol_both_failed", "双协议调用失败")

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
        # 部分网关在 thinking 变体上会直接断开连接，允许降级到基础模型名重试。
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
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": user_prompt,
                            }
                        ],
                    }
                ],
            }
            if strict_json:
                payload["temperature"] = 0
            return f"{base_url}/responses", payload

        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0 if strict_json else 0.7,
        }
        return f"{base_url}/chat/completions", payload

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
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_blocks},
            ],
            "temperature": 0 if strict_json else 0.7,
        }
        return f"{base_url}/chat/completions", payload

    def _should_retry_protocol(self, error: StyleFallbackError) -> bool:
        return error.reason in {"protocol_endpoint_not_supported", "protocol_incompatible"}

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

        # 部分兼容网关会把协议不支持包装成 5xx，需要允许协议回退而不是直接降级。
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
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        }
        request_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        upstream_request = request.Request(url=url, method="POST", headers=headers, data=request_data)
        try:
            with request.urlopen(upstream_request, timeout=35) as response:
                raw_text = response.read().decode("utf-8")
        except url_error.HTTPError as exc:
            status_code = int(getattr(exc, "code", 0) or 0)
            body_text = exc.read().decode("utf-8", errors="ignore")
            if status_code in {404, 405}:
                raise StyleFallbackError("protocol_endpoint_not_supported", f"HTTP {status_code}") from exc
            if self._looks_like_protocol_incompatible(status_code, body_text):
                raise StyleFallbackError("protocol_incompatible", f"HTTP {status_code}: {body_text[:120]}") from exc
            raise StyleFallbackError("upstream_http_error", f"HTTP {status_code}: {body_text[:120]}") from exc
        except (TimeoutError, url_error.URLError, OSError) as exc:
            raise StyleFallbackError("upstream_timeout_or_network", str(exc)) from exc
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise StyleFallbackError("upstream_invalid_json", "上游响应不是有效 JSON") from exc
        if not isinstance(parsed, dict):
            raise StyleFallbackError("upstream_invalid_payload", "上游响应结构非法")
        return parsed

    def build_user_facing_upstream_message(self, error: StyleFallbackError) -> str:
        reason = str(error.reason or "").strip()
        detail = self._normalize_upstream_error_detail(error.detail)
        lowered_detail = detail.lower()
        if reason in {"routing_unavailable", "text_provider_missing", "text_model_missing", "text_provider_unavailable"}:
            return "模型服务连接失败：模型配置不可用，请先检查模型设置"
        if reason in {"protocol_endpoint_not_supported", "protocol_incompatible"}:
            base = "模型服务连接失败：模型协议不兼容，请切换协议或模型后重试"
        elif reason == "upstream_timeout_or_network":
            base = "模型服务连接失败：模型服务连接超时，请稍后重试"
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
        if detail:
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

        return {
            "title": title.strip(),
            "items": normalized_items,
            "max": max_value,
        }

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
