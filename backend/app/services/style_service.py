from __future__ import annotations

import logging
from typing import Any

from backend.app.core.errors import DomainError, not_found
from backend.app.core.prompt_loader import render_prompt
from backend.app.core.utils import new_id, now_iso
from backend.app.domain.enums import STYLE_STAGE_ORDER
from backend.app.infra import http_client as http_client_module
from backend.app.infra.storage import Storage
from backend.app.repositories.asset_repo import AssetRepository
from backend.app.repositories.session_repo import SessionRepository
from backend.app.repositories.style_repo import StyleRepository
from backend.app.services.model_service import ModelService
from backend.app.services.style.constants import STAGE_OPTION_RULES
from backend.app.services.style.errors import StyleFallbackError
from backend.app.services.style.model_caller_mixin import StyleModelCallerMixin
from backend.app.services.style.payload_mixin import StylePayloadMixin
from backend.app.services.style.protocol_adapter_mixin import StyleProtocolAdapterMixin

logger = logging.getLogger(__name__)
request = http_client_module.request


class StyleService(StyleModelCallerMixin, StyleProtocolAdapterMixin, StylePayloadMixin):
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
            "fallback_used": False,
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
        try:
            payload = self._parse_model_payload(model_text)
            options = self._extract_and_validate_options(payload)
        except StyleFallbackError as exc:
            if not self._should_retry_strict_json(exc):
                raise
            logger.warning("风格对话进入严格 JSON 重试: reason=%s detail=%s", exc.reason, exc.detail)
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
        return render_prompt(
            "style/chat_system_prompt.txt",
            stage=stage,
            title=stage_rule["title"],
            max=stage_rule["max"],
            strict_suffix="本次重试必须只输出一个 JSON 对象，temperature=0，不允许解释文本。" if strict_json else "",
        )

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
            "请返回可直接渲染到前端的 JSON。"
        )
