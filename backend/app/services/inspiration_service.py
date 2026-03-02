
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from backend.app.core.errors import DomainError, not_found
from backend.app.core.utils import new_id, now_iso
from backend.app.infra.storage import Storage
from backend.app.repositories.asset_repo import AssetRepository
from backend.app.repositories.inspiration_repo import InspirationRepository
from backend.app.repositories.session_repo import SessionRepository
from backend.app.repositories.style_repo import StyleRepository
from backend.app.services.asset_service import AssetService, TranscriptService
from backend.app.services.model_service import ModelService
from backend.app.services.style_service import StyleFallbackError, StyleService

from backend.app.services.inspiration.constants import (
    LOCKED_HINT,
    STYLE_PROMPT_RETRY_SYSTEM_PROMPT,
    STYLE_PROMPT_SYSTEM_PROMPT,
)
from backend.app.services.inspiration.flow_mixin import InspirationFlowMixin
logger = logging.getLogger(__name__)


class InspirationService(InspirationFlowMixin):
    def __init__(
        self,
        inspiration_repo: InspirationRepository,
        session_repo: SessionRepository,
        asset_repo: AssetRepository,
        style_repo: StyleRepository,
        asset_service: AssetService,
        transcript_service: TranscriptService,
        style_service: StyleService,
        model_service: ModelService,
        storage: Storage,
    ):
        self.inspiration_repo = inspiration_repo
        self.session_repo = session_repo
        self.asset_repo = asset_repo
        self.style_repo = style_repo
        self.asset_service = asset_service
        self.transcript_service = transcript_service
        self.style_service = style_service
        self.model_service = model_service
        self.storage = storage

    def get_conversation(self, session_id: str) -> dict[str, Any]:
        session = self.session_repo.get(session_id)
        if not session:
            raise not_found("会话", session_id)
        state = self._ensure_state(session_id)
        self._ingest_ready_transcripts(session_id, state)
        self._ensure_welcome_message(session_id, state)
        return self._build_response(session_id, state)

    async def send_message(
        self,
        *,
        session_id: str,
        text: str | None,
        selected_items: list[str],
        action: str | None,
        image_usages: list[str],
        images: list[UploadFile],
        videos: list[UploadFile],
    ) -> dict[str, Any]:
        session = self.session_repo.get(session_id)
        if not session:
            raise not_found("会话", session_id)
        state = self._ensure_state(session_id)
        self._ingest_ready_transcripts(session_id, state)
        self._ensure_welcome_message(session_id, state)

        normalized_text = (text or "").strip()
        normalized_items = self._normalize_selected_items(selected_items)
        normalized_action = (action or "").strip() or None
        _ = image_usages
        if not normalized_text and not normalized_items and not normalized_action and not images and not videos:
            raise DomainError(code="E-1099", message="请输入内容或选择选项", status_code=400)

        if images:
            self._ensure_vision_capable()

        attachments = await self._save_attachments(
            session_id=session_id,
            text=normalized_text,
            images=images,
            videos=videos,
        )
        if normalized_action == "use_style_profile":
            user_content, attachments = self._build_use_style_profile_user_message(
                user_text=normalized_text,
                selected_items=normalized_items,
                attachments=attachments,
            )
        else:
            user_content = self._build_user_message(normalized_text, normalized_items, attachments)
        self._append_message(
            session_id=session_id,
            role="user",
            content=user_content,
            stage=state["stage"],
            attachments=attachments,
            options=None,
            fallback_used=False,
        )

        if state["locked"]:
            self._handle_locked_stage(session, state, normalized_items, normalized_action)
            return self._build_response(session_id, state)

        if normalized_action == "use_style_profile":
            self._handle_use_style_profile(session, state, normalized_text, normalized_items)
            return self._build_response(session_id, state)

        stage = state.get("stage", "style_collecting")
        if stage == "prompt_revision":
            self._handle_prompt_revision(
                session,
                state,
                normalized_text,
                normalized_items,
                normalized_action,
                attachments,
            )
            return self._build_response(session_id, state)
        if stage == "asset_confirming":
            self._handle_asset_confirming(session, state, normalized_text, normalized_items, normalized_action)
            return self._build_response(session_id, state)

        self._handle_collecting_stage(session_id, session, state, normalized_text, normalized_items)
        return self._build_response(session_id, state)

    def _handle_collecting_stage(
        self,
        session_id: str,
        session: dict[str, Any],
        state: dict[str, Any],
        user_text: str,
        selected_items: list[str],
    ) -> None:
        current_style_stage = state.get("style_stage", "painting_style")
        chat_result = self.style_service.chat(
            session_id=session_id,
            stage=current_style_stage,
            user_reply=user_text,
            selected_items=selected_items,
        )
        self._merge_style_payload(state, current_style_stage, selected_items)
        fallback_used = bool(chat_result.get("fallback_used"))
        if chat_result["is_finished"]:
            image_count = self._extract_image_count(selected_items, user_text, session_id=session_id)
            if image_count is None:
                state["style_stage"] = "image_count"
                state["stage"] = "prompt_revision"
                state["requirement_ready"] = False
                state["prompt_confirmable"] = False
                reply_text = self._build_collecting_requirement_reply(session, state, user_text)
                options = None
            else:
                state["image_count"] = image_count
                state["style_stage"] = "image_count"
                state["stage"] = "prompt_revision"
                state["requirement_ready"] = True
                generated_prompt = self._generate_style_prompt(session, state, "")
                state["style_prompt"] = generated_prompt
                options = self._resolve_prompt_action_options(session, state, user_text, generated_prompt)
                state["prompt_confirmable"] = bool(options)
                hint_suffix = "" if options else "\n\n请先补充地点、核心内容或画面重点，我再开放“确认提示词”。"
                reply_text = f"已生成风格提示词：\n\n{state['style_prompt']}{hint_suffix}"
        else:
            state["style_stage"] = chat_result.get("next_stage") or chat_result["stage"]
            state["stage"] = "style_collecting"
            reply_text = chat_result["reply"]
            options = chat_result["options"]
        state["updated_at"] = now_iso()
        self.inspiration_repo.upsert_state(state)
        self._append_message(
            session_id=session_id,
            role="assistant",
            content=reply_text,
            stage=state["stage"],
            attachments=[],
            options=options,
            fallback_used=fallback_used,
            style_context=self._build_style_context(state),
        )

    def _handle_prompt_revision(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        user_text: str,
        selected_items: list[str],
        action: str | None,
        attachments: list[dict[str, Any]],
    ) -> None:
        has_attachment_input = bool(attachments)
        control_items = {"确定使用", "确认提示词"}
        material_selected_items = [
            item for item in selected_items if isinstance(item, str) and item.strip() and item.strip() not in control_items
        ]
        feedback_text = user_text.strip()
        selected_feedback = "、".join(material_selected_items)
        semantic_feedback_text = "；".join(part for part in (feedback_text, selected_feedback) if part)
        has_user_feedback = bool(semantic_feedback_text) or has_attachment_input
        should_detect_image_count = bool(selected_items) or bool(user_text.strip())
        detected_image_count = (
            self._extract_image_count(selected_items, user_text, session_id=session["id"])
            if should_detect_image_count
            else None
        )
        if detected_image_count is not None:
            state["image_count"] = detected_image_count
        if not isinstance(state.get("image_count"), int):
            state["requirement_ready"] = False
            state["prompt_confirmable"] = False
            state["updated_at"] = now_iso()
            self.inspiration_repo.upsert_state(state)
            self._append_message(
                session_id=session["id"],
                role="assistant",
                content=self._build_collecting_requirement_reply(session, state, user_text),
                stage=state["stage"],
                attachments=[],
                options=None,
                fallback_used=False,
                style_context=self._build_style_context(state),
            )
            return
        confirm_prompt = action == "confirm_prompt" or "确定使用" in selected_items
        if confirm_prompt:
            feedback_parts: list[str] = []
            if semantic_feedback_text:
                feedback_parts.append(semantic_feedback_text)
            if has_attachment_input:
                feedback_parts.append("本轮补充了参考附件，请结合附件细化提示词。")
            confirm_feedback = "；".join(part for part in feedback_parts if part)
            if confirm_feedback:
                revised_prompt = self._generate_style_prompt(session, state, confirm_feedback)
                state["style_prompt"] = revised_prompt
                state["requirement_ready"] = True
            if not bool(state.get("requirement_ready")):
                state["updated_at"] = now_iso()
                self.inspiration_repo.upsert_state(state)
                self._append_message(
                    session_id=session["id"],
                    role="assistant",
                    content="先别急着确认提示词，请先告诉我本次要介绍的城市/景点/美食，以及计划生成几张图。",
                    stage=state["stage"],
                    attachments=[],
                    options=None,
                    fallback_used=False,
                    style_context=self._build_style_context(state),
                )
                return
            # 按钮已展示代表此前已通过可确认判定，避免点击时再次判定出现抖动。
            if not bool(state.get("prompt_confirmable")) and not self._assess_prompt_readiness(
                session, state, confirm_feedback or user_text, state.get("style_prompt", "")
            ):
                state["updated_at"] = now_iso()
                self.inspiration_repo.upsert_state(state)
                self._append_message(
                    session_id=session["id"],
                    role="assistant",
                    content="当前信息还不够完整，请继续补充地点、核心美食/景点、画面重点或张数后再确认提示词。",
                    stage=state["stage"],
                    attachments=[],
                    options=None,
                    fallback_used=False,
                    style_context=self._build_style_context(state),
                )
                return
            state["asset_candidates"] = self._extract_asset_candidates(
                session["id"],
                confirm_feedback or user_text,
                str(state.get("style_prompt") or ""),
            )
            state["allocation_plan"] = self._build_allocation_plan(
                session=session,
                state=state,
                user_hint=confirm_feedback or user_text,
            )
            state["stage"] = "asset_confirming"
            state["prompt_confirmable"] = False
            state["updated_at"] = now_iso()
            self.inspiration_repo.upsert_state(state)
            self._append_message(
                session_id=session["id"],
                role="assistant",
                content=self._build_asset_confirm_reply(state),
                stage=state["stage"],
                attachments=[],
                options={"title": "请选择下一步", "items": ["确认分图并锁定", "继续调整分图"], "max": 1},
                fallback_used=False,
                asset_candidates=state.get("asset_candidates"),
                style_context=self._build_style_context(state),
            )
            return

        feedback_parts: list[str] = []
        if semantic_feedback_text:
            feedback_parts.append(semantic_feedback_text)
        if has_attachment_input:
            feedback_parts.append("本轮补充了参考附件，请结合附件细化提示词。")
        feedback = "；".join(part for part in feedback_parts if part) or "请继续优化提示词细节"
        revised_prompt = self._generate_style_prompt(session, state, feedback)
        state["style_prompt"] = revised_prompt
        if has_user_feedback:
            state["requirement_ready"] = True
        options = self._resolve_prompt_action_options(session, state, feedback, revised_prompt)
        state["prompt_confirmable"] = bool(options)
        hint_suffix = "" if options else "\n\n请继续补充关键需求，补齐后我会开放“确认提示词”。"
        state["updated_at"] = now_iso()
        self.inspiration_repo.upsert_state(state)
        self._append_message(
            session_id=session["id"],
            role="assistant",
            content=f"已根据你的反馈修订提示词：\n\n{revised_prompt}{hint_suffix}",
            stage=state["stage"],
            attachments=[],
            options=options,
            fallback_used=False,
            style_context=self._build_style_context(state),
        )
    def _handle_asset_confirming(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        user_text: str,
        selected_items: list[str],
        action: str | None,
    ) -> None:
        confirm_assets = (
            action in {"confirm_assets", "confirm_allocation_plan"}
            or "确认资产并锁定" in selected_items
            or "确认分图并锁定" in selected_items
            or "确认资产" in selected_items
        )
        revise_assets = (
            action in {"revise_assets", "revise_allocation_plan"}
            or "继续调整资产" in selected_items
            or "继续调整分图" in selected_items
        )
        if confirm_assets:
            state["allocation_plan"] = self._mark_allocation_plan_confirmed(state.get("allocation_plan"))
            style_profile_id = self._upsert_draft_style(session, state)
            state["locked"] = True
            state["stage"] = "locked"
            state["prompt_confirmable"] = False
            state["draft_style_id"] = style_profile_id
            state["updated_at"] = now_iso()
            self.inspiration_repo.upsert_state(state)
            self._append_message(
                session_id=session["id"],
                role="assistant",
                content=LOCKED_HINT,
                stage=state["stage"],
                attachments=[],
                options={"title": "是否保存风格", "items": ["保存风格", "暂不保存"], "max": 1},
                fallback_used=False,
                style_context=self._build_style_context(state),
            )
            return

        if revise_assets and user_text:
            revised = self._extract_asset_candidates(
                session["id"],
                user_text,
                str(state.get("style_prompt") or ""),
            )
            state["asset_candidates"] = revised
        elif user_text:
            state["asset_candidates"] = self._extract_asset_candidates(
                session["id"],
                user_text,
                str(state.get("style_prompt") or ""),
            )

        state["allocation_plan"] = self._build_allocation_plan(
            session=session,
            state=state,
            user_hint=user_text,
        )

        state["updated_at"] = now_iso()
        self.inspiration_repo.upsert_state(state)
        self._append_message(
            session_id=session["id"],
            role="assistant",
            content=self._build_asset_confirm_reply(state),
            stage=state["stage"],
            attachments=[],
            options={"title": "请选择下一步", "items": ["确认分图并锁定", "继续调整分图"], "max": 1},
            fallback_used=False,
            asset_candidates=state.get("asset_candidates"),
            style_context=self._build_style_context(state),
        )

    def _handle_use_style_profile(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        user_text: str,
        selected_items: list[str],
    ) -> None:
        style_id = selected_items[0] if selected_items else user_text
        style_id = style_id.strip()
        if not style_id:
            raise DomainError(code="E-1099", message="请选择要应用的风格", status_code=400)
        profile = self.style_repo.get(style_id)
        if not profile:
            raise not_found("风格", style_id)
        payload = self.style_service._normalize_style_payload(profile.get("style_payload") or {})
        state["style_payload"] = payload
        state["style_prompt"] = payload.get("style_prompt") or state.get("style_prompt") or ""
        state["stage"] = "prompt_revision"
        state["requirement_ready"] = False
        state["draft_style_id"] = profile["id"]
        state["allocation_plan"] = []
        state["updated_at"] = now_iso()
        self.inspiration_repo.upsert_state(state)
        guidance_reply = self._build_style_requirement_reply(session, state, profile)
        self._append_message(
            session_id=session["id"],
            role="assistant",
            content=guidance_reply,
            stage=state["stage"],
            attachments=[],
            options=None,
            fallback_used=False,
            style_context=self._build_style_context(state, profile),
        )

    def _handle_locked_stage(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        selected_items: list[str],
        action: str | None,
    ) -> None:
        should_save = action == "save_style" or "保存风格" in selected_items
        should_skip = action == "skip_save" or "暂不保存" in selected_items
        if should_save:
            self._create_saved_style(session, state)
            reply_text = "已保存风格参数和提示词，可在风格列表中管理。"
        elif should_skip:
            reply_text = "已跳过保存，可直接开始生成。"
        else:
            reply_text = "当前方案已锁定，可直接开始生成。"
        state["updated_at"] = now_iso()
        self.inspiration_repo.upsert_state(state)
        self._append_message(
            session_id=session["id"],
            role="assistant",
            content=reply_text,
            stage=state["stage"],
            attachments=[],
            options=None,
            fallback_used=False,
            style_context=self._build_style_context(state),
        )

    async def _save_attachments(
        self,
        session_id: str,
        text: str,
        images: list[UploadFile],
        videos: list[UploadFile],
    ) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []
        if text:
            text_asset = self.asset_service.create_text_asset(session_id, "text", text)
            attachments.append(self._build_attachment(text_asset["id"], "text", "文本", None, "ready"))
        for image in images:
            image_name = image.filename or "upload.png"
            image_suffix = Path(image_name).suffix or ".png"
            image_file_name = f"{session_id}_{new_id()}{image_suffix}"
            image_content = await image.read()
            image_path = self.storage.save_image(image_file_name, image_content)
            image_preview_url = self.style_service._build_public_image_url(image_path)
            image_asset = self.asset_repo.create(
                {
                    "id": new_id(),
                    "session_id": session_id,
                    "asset_type": "image",
                    "content": image_name,
                    "file_path": image_path,
                    "status": "ready",
                    "created_at": now_iso(),
                }
            )
            attachments.append(
                self._build_attachment(
                    image_asset["id"],
                    "image",
                    image_name,
                    image_preview_url,
                    "ready",
                    usage_type="content_asset",
                )
            )
        for video in videos:
            video_name = video.filename or "upload.mp4"
            video_suffix = Path(video_name).suffix or ".mp4"
            video_file_name = f"{session_id}_{new_id()}{video_suffix}"
            video_content = await video.read()
            video_path = self.storage.save_video(video_file_name, video_content)
            video_asset = self.transcript_service.create_video_asset(
                session_id=session_id,
                file_path=video_path,
                file_name=video_name,
            )
            attachments.append(
                self._build_attachment(
                    video_asset["id"],
                    "video",
                    video_name,
                    video_path,
                    "processing",
                )
            )
        return attachments

    def _build_attachment(
        self,
        asset_id: str,
        attachment_type: str,
        name: str | None,
        preview_url: str | None,
        status: str,
        usage_type: str | None = None,
    ) -> dict[str, Any]:
        attachment = {
            "id": asset_id,
            "asset_id": asset_id,
            "type": attachment_type,
            "name": name,
            "preview_url": preview_url,
            "status": status,
            "usage_type": usage_type,
        }
        return attachment

    def _generate_style_prompt(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        feedback: str,
    ) -> str:
        style_payload = self._build_style_payload(state)
        image_count = state.get("image_count") or 1
        style_text = self._format_style_payload_text(style_payload)
        requirement_hint = self._build_prompt_requirement_hint(session["id"], feedback, state=state)
        context_prefix = (
            f"张数：{image_count}；"
            f"风格参数：{style_text}；修订意见：{feedback or '无'}；"
            f"用户硬性要求：{requirement_hint}。"
        )
        split_requirement = self._build_split_prompt_requirement(image_count)
        session_image_urls = self._collect_session_image_asset_urls(session["id"])
        if session_image_urls:
            model_text = self._call_vision_model_with_retry(
                session_id=session["id"],
                system_prompt=STYLE_PROMPT_SYSTEM_PROMPT,
                user_prompt=f"{context_prefix}{split_requirement}",
                image_urls=session_image_urls,
                strict_json=False,
            )
        else:
            model_text = self._call_text_model_with_retry(
                session_id=session["id"],
                system_prompt=STYLE_PROMPT_SYSTEM_PROMPT,
                user_prompt=f"{context_prefix}{split_requirement}",
                strict_json=False,
            )
        normalized_text = self._normalize_generated_prompt(model_text, image_count=image_count)
        if normalized_text:
            return normalized_text
        if session_image_urls:
            retry_text = self._call_vision_model_with_retry(
                session_id=session["id"],
                system_prompt=STYLE_PROMPT_RETRY_SYSTEM_PROMPT,
                user_prompt=f"请把下面需求改写成母提示词正文：{context_prefix}{split_requirement}",
                image_urls=session_image_urls,
                strict_json=False,
            )
        else:
            retry_text = self._call_text_model_with_retry(
                session_id=session["id"],
                system_prompt=STYLE_PROMPT_RETRY_SYSTEM_PROMPT,
                user_prompt=f"请把下面需求改写成母提示词正文：{context_prefix}{split_requirement}",
                strict_json=False,
            )
        normalized_retry_text = self._normalize_generated_prompt(retry_text, image_count=image_count)
        if normalized_retry_text:
            return normalized_retry_text
        raise DomainError(code="E-1004", message="模型输出格式异常，请重试", status_code=503)

    def _build_prompt_requirement_hint(
        self,
        session_id: str,
        feedback: str,
        state: dict[str, Any] | None = None,
    ) -> str:
        feedback_text = feedback.strip()
        parts: list[str] = []
        if feedback_text:
            parts.append(f"本轮补充：{feedback_text.replace('\n', ' ').strip()}")
        recent_context = self._collect_recent_user_context(session_id, limit=8)
        if recent_context:
            parts.append(f"历史需求：{recent_context.replace('\n', ' ').strip()}")
        if parts:
            return "；".join(parts)[:420]
        return "无"

    def _build_split_prompt_requirement(self, image_count: int) -> str:
        if image_count <= 1:
            return "请输出 1 段提示词，并以“生成一张”开头。"
        return (
            f"请严格输出 {image_count} 段分图提示词。"
            "每段都必须以“生成一张”开头，且各段分别描述不同图的主体与重点。"
            "禁止写“生成两张/生成三张/一次生成多张”。"
        )

    def _normalize_generated_prompt(self, model_text: str, *, image_count: int) -> str:
        prompt_text = model_text.strip()
        if not prompt_text:
            return ""
        if prompt_text.startswith("{") and prompt_text.endswith("}"):
            return ""
        if prompt_text.startswith("```"):
            lines = [line for line in prompt_text.splitlines() if not line.strip().startswith("```")]
            prompt_text = "\n".join(lines).strip()
        if not prompt_text:
            return ""
        if self._looks_like_internal_parameter_dump(prompt_text):
            return ""
        if not self._validate_split_prompt_format(prompt_text, image_count=image_count):
            return ""
        return prompt_text

    def _validate_split_prompt_format(self, prompt_text: str, *, image_count: int) -> bool:
        if image_count <= 1:
            return True
        compact = prompt_text.replace(" ", "")
        if "生成两张" in compact or "生成三张" in compact or "生成四张" in compact or "一次生成多张" in compact:
            return False
        lines = [line.strip(" -•\t") for line in prompt_text.splitlines() if line.strip()]
        one_image_lines = [line for line in lines if line.startswith("生成一张")]
        return len(one_image_lines) >= image_count

    def _looks_like_internal_parameter_dump(self, text: str) -> bool:
        lowered = text.lower()
        markers = ("风格参数", "修订意见", "prompt_prefix", "style_payload")
        if any(marker in text for marker in markers):
            return True
        return "json" in lowered and "{" in text and "}" in text

    def _call_text_model_with_retry(
        self,
        *,
        session_id: str,
        system_prompt: str,
        user_prompt: str,
        strict_json: bool,
    ) -> str:
        provider, model_name = self.style_service._resolve_text_model_provider()
        last_error: StyleFallbackError | None = None
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                return self.style_service._call_text_model(
                    provider,
                    model_name,
                    system_prompt,
                    user_prompt,
                    strict_json=strict_json,
                )
            except StyleFallbackError as error:
                last_error = error
                retryable = self._is_retryable_model_error(error)
                logger.warning(
                    "文本模型调用失败: session_id=%s attempt=%s reason=%s detail=%s retryable=%s",
                    session_id,
                    attempt + 1,
                    error.reason,
                    error.detail,
                    retryable,
                )
                if attempt == max_attempts - 1 or not retryable:
                    break
        raise DomainError(
            code="E-1004",
            message=self.style_service.build_user_facing_upstream_message(
                last_error or StyleFallbackError("unknown", "模型服务调用失败")
            ),
            status_code=503,
            details={"reason": last_error.reason if last_error else "unknown"},
        )

    def _is_retryable_model_error(self, error: StyleFallbackError) -> bool:
        retryable_reasons = {
            "upstream_timeout_or_network",
            "upstream_http_error",
            "upstream_invalid_json",
            "upstream_invalid_payload",
            "upstream_empty_text",
            "protocol_both_failed",
        }
        return error.reason in retryable_reasons


