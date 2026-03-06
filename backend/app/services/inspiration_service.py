
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from backend.app.agent import CreativeAgent
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
        generation_worker: Any | None = None,
        creative_agent: CreativeAgent | None = None,
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
        self.generation_worker = generation_worker
        self.creative_agent = creative_agent
        self._agent_meta_by_session: dict[str, dict[str, Any]] = {}

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
        if not normalized_text and not normalized_items and not normalized_action and not images and not videos:
            raise DomainError(code="E-1099", message="请输入内容或选择选项", status_code=400)

        if images:
            self._ensure_vision_capable()

        attachments = await self._save_attachments(
            session_id=session_id,
            text=normalized_text,
            image_usages=image_usages,
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
        agent_turn = self._run_agent_turn(
            session=session,
            state=state,
            text=normalized_text,
            selected_items=normalized_items,
            action=normalized_action,
            attachments=attachments,
        )
        self._apply_agent_turn(session_id=session_id, state=state, turn=agent_turn)
        return self._build_response(session_id, state)

    def _run_agent_turn(
        self,
        *,
        session: dict[str, Any],
        state: dict[str, Any],
        text: str,
        selected_items: list[str],
        action: str | None,
        attachments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.creative_agent:
            raise DomainError(code="E-1006", message="Agent 模式尚未初始化", status_code=503)
        request_payload = {
            "session_id": session["id"],
            "text": text,
            "selected_items": selected_items,
            "action": action,
            "attachments": attachments,
            "content_mode": session.get("content_mode"),
            "state": {
                "stage": state.get("stage", "style_collecting"),
                "locked": bool(state.get("locked")),
                "content_mode": session.get("content_mode"),
                "style_payload": self._build_style_payload(state),
                "style_prompt": str(state.get("style_prompt") or ""),
                "asset_candidates": state.get("asset_candidates") if isinstance(state.get("asset_candidates"), dict) else {},
                "image_count": state.get("image_count"),
                "allocation_plan": state.get("allocation_plan") if isinstance(state.get("allocation_plan"), list) else [],
                "draft_style_id": state.get("draft_style_id"),
            },
        }
        self._active_agent_session_id = session["id"]
        try:
            return self.creative_agent.respond(request_payload)
        finally:
            self._active_agent_session_id = None

    def _apply_agent_turn(
        self,
        *,
        session_id: str,
        state: dict[str, Any],
        turn: dict[str, Any],
    ) -> None:
        turn_payload = dict(turn)
        if "style_payload" in turn_payload and turn_payload.get("style_payload") is not None:
            state["style_payload"] = self.style_service._normalize_style_payload(turn_payload["style_payload"])
        if "style_prompt" in turn_payload and turn_payload.get("style_prompt") is not None:
            state["style_prompt"] = str(turn_payload.get("style_prompt") or "").strip()
        if "image_count" in turn_payload and turn_payload.get("image_count") is not None:
            state["image_count"] = turn_payload.get("image_count")
        if "asset_candidates" in turn_payload and turn_payload.get("asset_candidates") is not None:
            state["asset_candidates"] = turn_payload.get("asset_candidates") or {}
        if "allocation_plan" in turn_payload and isinstance(turn_payload.get("allocation_plan"), list):
            state["allocation_plan"] = turn_payload.get("allocation_plan") or []
        if "draft_style_id" in turn_payload and turn_payload.get("draft_style_id") is not None:
            state["draft_style_id"] = turn_payload.get("draft_style_id")
        if "requirement_ready" in turn_payload and turn_payload.get("requirement_ready") is not None:
            state["requirement_ready"] = bool(turn_payload.get("requirement_ready"))
        if "prompt_confirmable" in turn_payload and turn_payload.get("prompt_confirmable") is not None:
            state["prompt_confirmable"] = bool(turn_payload.get("prompt_confirmable"))
        if "stage" in turn_payload and turn_payload.get("stage") is not None:
            state["stage"] = str(turn_payload.get("stage") or state.get("stage") or "style_collecting")
        if "locked" in turn_payload and turn_payload.get("locked") is not None:
            state["locked"] = bool(turn_payload["locked"])
        state["updated_at"] = now_iso()
        self.inspiration_repo.upsert_state(state)
        reply_text = str(turn_payload.get("reply") or "").strip() or "Agent 已处理当前请求。"
        self._append_message(
            session_id=session_id,
            role="assistant",
            content=reply_text,
            stage=state["stage"],
            attachments=[],
            options=turn_payload.get("options"),
            fallback_used=False,
            asset_candidates=turn_payload.get("asset_candidates"),
            style_context=self._build_style_context(state),
        )
        self._set_agent_meta(
            session_id,
            {
                "mode": "langgraph",
                "dynamic_stage": turn_payload.get("dynamic_stage"),
                "dynamic_stage_label": turn_payload.get("dynamic_stage_label"),
                "trace": turn_payload.get("trace") or [],
            },
        )

    def _set_agent_meta(self, session_id: str, meta: dict[str, Any]) -> None:
        self._agent_meta_by_session[session_id] = meta

    def _build_agent_meta(self, session_id: str, state: dict[str, Any]) -> dict[str, Any]:
        cached = self._agent_meta_by_session.get(session_id)
        if cached:
            return cached
        return {
            "mode": "langgraph",
            "dynamic_stage": state.get("stage"),
            "dynamic_stage_label": None,
            "trace": [],
        }

    def suggest_painting_style(self, *, stage: str, user_reply: str, selected_items: list[str]) -> dict[str, Any]:
        session_id = str(getattr(self, "_active_agent_session_id", "") or "").strip()
        if not session_id:
            raise DomainError(code="E-1099", message="Agent 缺少会话上下文", status_code=500)
        return self.style_service.chat(
            session_id=session_id,
            stage=stage,
            user_reply=user_reply,
            selected_items=selected_items,
        )

    def extract_assets(self, *, session_id: str, user_hint: str, style_prompt: str) -> dict[str, Any]:
        return self._extract_asset_candidates(session_id, user_hint, style_prompt)

    def generate_style_prompt(self, *, session_id: str, feedback: str) -> dict[str, Any]:
        session = self.session_repo.get(session_id)
        if not session:
            raise not_found("会话", session_id)
        state = self._ensure_state(session_id)
        prompt_text = self._generate_style_prompt(session, state, feedback)
        return {
            "style_prompt": prompt_text,
            "image_count": state.get("image_count"),
        }

    def allocate_assets_to_images(self, *, session_id: str, user_hint: str) -> list[dict[str, Any]]:
        session = self.session_repo.get(session_id)
        if not session:
            raise not_found("会话", session_id)
        state = self._ensure_state(session_id)
        return self._build_allocation_plan(session=session, state=state, user_hint=user_hint)

    def save_style_from_agent(self, session_id: str) -> dict[str, Any]:
        session = self.session_repo.get(session_id)
        if not session:
            raise not_found("会话", session_id)
        state = self._ensure_state(session_id)
        if not bool(state.get("locked")):
            raise DomainError(code="E-1099", message="当前方案尚未锁定，不能保存风格", status_code=400)
        saved_style = self._create_saved_style(session, state)
        return {
            "style_id": saved_style["id"],
            "style_name": saved_style["name"],
            "status": "saved",
        }

    def generate_images(self, *, session_id: str) -> dict[str, Any]:
        session = self.session_repo.get(session_id)
        if not session:
            raise not_found("会话", session_id)
        state = self._ensure_state(session_id)
        if not bool(state.get("locked")):
            raise DomainError(code="E-1099", message="当前方案尚未锁定，不能开始生成", status_code=400)
        if not self.generation_worker:
            raise DomainError(code="E-1099", message="生成 Worker 不可用", status_code=500)
        style_profile_id = str(state.get("draft_style_id") or "").strip()
        if not style_profile_id:
            raise DomainError(code="E-1099", message="当前草案还没有可生成的风格配置", status_code=400)
        if not self.style_repo.get(style_profile_id):
            raise not_found("风格", style_profile_id)
        image_count = state.get("image_count")
        if not isinstance(image_count, int) or image_count < 1 or image_count > 10:
            raise DomainError(code="E-1099", message="当前草案缺少合法的图片数量", status_code=400)
        now = now_iso()
        job = {
            "id": new_id(),
            "session_id": session_id,
            "style_profile_id": style_profile_id,
            "image_count": image_count,
            "status": "queued",
            "progress_percent": 0,
            "current_stage": "asset_extract",
            "stage_message": "任务已创建",
            "error_code": None,
            "error_message": None,
            "created_at": now,
            "updated_at": now,
        }
        self.generation_worker.job_repo.create_with_initial_log(job, log_id=new_id())
        self.generation_worker.schedule(job["id"])
        return {"job_id": job["id"], "status": "queued"}

    def generate_copy(self, *, job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "status": "queued"}

    async def _save_attachments(
        self,
        session_id: str,
        text: str,
        image_usages: list[str],
        images: list[UploadFile],
        videos: list[UploadFile],
    ) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []
        if text:
            text_asset = self.asset_service.create_text_asset(session_id, "text", text)
            attachments.append(self._build_attachment(text_asset["id"], "text", "文本", None, "ready"))
        for index, image in enumerate(images):
            image_name = image.filename or "upload.png"
            image_suffix = Path(image_name).suffix or ".png"
            image_file_name = f"{session_id}_{new_id()}{image_suffix}"
            image_content = await image.read()
            image_path = self.storage.save_image(image_file_name, image_content)
            image_preview_url = self.style_service._build_public_image_url(image_path)
            usage_type = self._normalize_image_usage(
                image_usages[index] if index < len(image_usages) else None,
            )
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
                    usage_type=usage_type,
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

    def _normalize_image_usage(self, raw_value: str | None) -> str:
        if isinstance(raw_value, str) and raw_value.strip() == "style_reference":
            return "style_reference"
        return "content_asset"

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
        multi_pattern = re.compile(r"生成[两二三四五六七八九十\d]+张")
        if multi_pattern.search(compact) or "一次生成多张" in compact:
            return False
        lines = [line.strip(" -•\t") for line in prompt_text.splitlines() if line.strip()]
        one_image_lines = [line for line in lines if line.startswith("生成一张")]
        return len(one_image_lines) >= image_count

    def _looks_like_internal_parameter_dump(self, text: str) -> bool:
        markers = ("风格参数", "修订意见", "prompt_prefix", "style_payload")
        if any(marker in text for marker in markers):
            return True
        return False

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


