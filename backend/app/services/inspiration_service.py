
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterator

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

from backend.app.services.inspiration.flow_mixin import InspirationFlowMixin
from backend.app.services.inspiration.prompt_generation_mixin import InspirationPromptGenerationMixin
logger = logging.getLogger(__name__)


class InspirationService(InspirationPromptGenerationMixin, InspirationFlowMixin):
    _IMAGE_COUNT_ACTION_PATTERN = re.compile(r"^(?:confirm|select|set)_image_count_(\d+)$")
    _TEXTUAL_IMAGE_COUNT_PATTERN = re.compile(
        r"(?<!第)(?:一共|总共|共|想做|想要|要做|做|要|生成|出|来|安排|需要)?\s*(10|[1-9]|[一二两三四五六七八九十])\s*张(?:图|图片|配图|海报|图文)?"
    )
    _CHINESE_IMAGE_COUNT_MAP = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }

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
        new_transcripts = self._ingest_ready_transcripts(session_id, state)
        self._ensure_welcome_message(session_id, state)
        transcript_text = "\n".join(
            str(asset.get("content") or "").strip()
            for asset in new_transcripts
            if str(asset.get("content") or "").strip()
        )
        transcript_attachments = [
            self._build_attachment(
                str(asset.get("id") or ""),
                "transcript",
                "视频转写",
                asset.get("file_path"),
                "ready",
            )
            for asset in new_transcripts
            if str(asset.get("id") or "").strip()
        ]
        state = self._backfill_asset_candidates_from_transcripts(
            session_id=session_id,
            state=state,
            transcript_text=transcript_text,
        )
        if new_transcripts and self._should_autorun_from_transcripts(new_transcripts):
            self._apply_agent_turn(
                session_id=session_id,
                state=state,
                turn=self._run_agent_turn(
                    session=session,
                    state=state,
                    text=transcript_text,
                    selected_items=[],
                    action="transcript_ready_auto",
                    attachments=transcript_attachments,
                ),
            )
        return self._build_response(session_id, state)

    def _backfill_asset_candidates_from_transcripts(
        self,
        *,
        session_id: str,
        state: dict[str, Any],
        transcript_text: str,
    ) -> dict[str, Any]:
        existing_asset_candidates = state.get("asset_candidates")
        if isinstance(existing_asset_candidates, dict) and existing_asset_candidates:
            return state

        should_backfill = str(state.get("stage") or "") == "count_confirmation_required"
        if not transcript_text and should_backfill:
            transcript_text = "\n".join(
                str(asset.get("content") or "").strip()
                for asset in self.asset_repo.list_by_session(session_id)
                if asset.get("asset_type") == "transcript" and str(asset.get("content") or "").strip()
            )
        if not transcript_text:
            return state

        try:
            state["asset_candidates"] = self.extract_assets(
                session_id=session_id,
                user_hint=transcript_text,
                style_prompt=str(state.get("style_prompt") or ""),
            )
            state["updated_at"] = now_iso()
            return self.inspiration_repo.upsert_state(state)
        except (DomainError, StyleFallbackError):
            logger.warning("转写资产候选补全失败，回退到常规 Agent 流程: session_id=%s", session_id)
            return state

    async def send_message(self, *, session_id: str, text: str | None, selected_items: list[str], action: str | None, image_usages: list[str], images: list[UploadFile], videos: list[UploadFile]) -> dict[str, Any]:
        prepared = await self._prepare_message_context(
            session_id=session_id,
            text=text,
            selected_items=selected_items,
            action=action,
            image_usages=image_usages,
            images=images,
            videos=videos,
        )
        if prepared.get("defer_for_transcript"):
            self._apply_agent_turn(session_id=session_id, state=prepared["state"], turn=self._build_video_transcribing_turn()); return self._build_response(session_id, prepared["state"])
        agent_turn = self._run_agent_turn(
            session=prepared["session"],
            state=prepared["state"],
            text=prepared["normalized_text"],
            selected_items=prepared["normalized_items"],
            action=prepared["normalized_action"],
            attachments=prepared["attachments"],
        )
        self._apply_agent_turn(session_id=session_id, state=prepared["state"], turn=agent_turn)
        return self._build_response(session_id, prepared["state"])

    async def send_message_stream(self, *, session_id: str, text: str | None, selected_items: list[str], action: str | None, image_usages: list[str], images: list[UploadFile], videos: list[UploadFile]) -> Iterator[str]:
        prepared = await self._prepare_message_context(
            session_id=session_id,
            text=text,
            selected_items=selected_items,
            action=action,
            image_usages=image_usages,
            images=images,
            videos=videos,
        )
        if prepared.get("defer_for_transcript"):
            self._apply_agent_turn(session_id=session_id, state=prepared["state"], turn=self._build_video_transcribing_turn())
            return iter([self._format_sse_event("done", self._build_response(session_id, prepared["state"]))])
        request_payload = self._build_agent_request_payload(
            session=prepared["session"],
            state=prepared["state"],
            text=prepared["normalized_text"],
            selected_items=prepared["normalized_items"],
            action=prepared["normalized_action"],
            attachments=prepared["attachments"],
        )

        def event_stream() -> Iterator[str]:
            try:
                if not self.creative_agent:
                    raise DomainError(code="E-1006", message="Agent 模式尚未初始化", status_code=503)
                for event in self.creative_agent.respond_stream(request_payload):
                    event_type = str(event.get("event") or "").strip()
                    data = event.get("data")
                    if event_type == "result":
                        self._apply_agent_turn(session_id=session_id, state=prepared["state"], turn=dict(data or {}))
                        response = self._build_response(session_id, prepared["state"])
                        yield self._format_sse_event("done", response)
                        return
                    if event_type == "error":
                        yield self._format_sse_event("error", self._sanitize_stream_error_payload(data))
                        return
                    if event_type:
                        yield self._format_sse_event(event_type, data or {})
            except DomainError as exc:
                yield self._format_sse_event("error", self._sanitize_stream_error_payload({"code": exc.code, "message": exc.message}))
            except Exception:
                logger.exception("灵感对话 SSE 流执行失败: session_id=%s", session_id)
                yield self._format_sse_event("error", {"code": "E-1099", "message": "Agent 执行异常"})

        return event_stream()

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
        request_payload = self._build_agent_request_payload(
            session=session,
            state=state,
            text=text,
            selected_items=selected_items,
            action=action,
            attachments=attachments,
        )
        return self.creative_agent.respond(request_payload)

    def _build_agent_request_payload(self, *, session: dict[str, Any], state: dict[str, Any], text: str, selected_items: list[str], action: str | None, attachments: list[dict[str, Any]]) -> dict[str, Any]:
        selected_style_profile = self._resolve_selected_style_profile(action=action, selected_items=selected_items); latest_assistant_context = self._get_latest_assistant_turn_context(session["id"])
        return {
            "session_id": session["id"],
            "text": text,
            "selected_items": selected_items,
            "action": action,
            "attachments": attachments,
            "content_mode": session.get("content_mode"),
            "selected_style_profile": selected_style_profile,
            "latest_assistant_reply": latest_assistant_context.get("reply"), "latest_assistant_options": latest_assistant_context.get("options"), "latest_assistant_stage": latest_assistant_context.get("stage"),
            "state": {
                "stage": state.get("stage", "initial_understanding"),
                "style_stage": state.get("style_stage"),
                "locked": bool(state.get("locked")),
                "content_mode": session.get("content_mode"),
                "style_payload": self._build_style_payload(state),
                "style_prompt": str(state.get("style_prompt") or ""),
                "asset_candidates": state.get("asset_candidates") if isinstance(state.get("asset_candidates"), dict) else {},
                "image_count": state.get("image_count"),
                "allocation_plan": state.get("allocation_plan") if isinstance(state.get("allocation_plan"), list) else [],
                "draft_style_id": state.get("draft_style_id"),
                "progress": state.get("progress"),
                "progress_label": state.get("progress_label"),
                "active_job_id": state.get("active_job_id"),
            },
        }

    def _apply_agent_turn(
        self,
        *,
        session_id: str,
        state: dict[str, Any],
        turn: dict[str, Any],
    ) -> None:
        turn_payload = dict(turn)
        normalized_options = self._normalize_agent_options(turn_payload["options"]) if "options" in turn_payload else None
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
        if "style_stage" in turn_payload and turn_payload.get("style_stage") is not None:
            state["style_stage"] = str(turn_payload.get("style_stage") or "painting_style")
        if "requirement_ready" in turn_payload and turn_payload.get("requirement_ready") is not None:
            state["requirement_ready"] = bool(turn_payload.get("requirement_ready"))
        if "prompt_confirmable" in turn_payload and turn_payload.get("prompt_confirmable") is not None:
            state["prompt_confirmable"] = bool(turn_payload.get("prompt_confirmable"))
        if "stage" in turn_payload and turn_payload.get("stage") is not None:
            state["stage"] = str(turn_payload.get("stage") or state.get("stage") or "initial_understanding")
        if "locked" in turn_payload and turn_payload.get("locked") is not None:
            state["locked"] = self._normalize_locked_state(
                stage=str(state.get("stage") or turn_payload.get("stage") or ""),
                requested_locked=bool(turn_payload["locked"]),
                state=state,
                turn_payload=turn_payload,
            )
        if "progress" in turn_payload:
            state["progress"] = self._normalize_progress_value(turn_payload.get("progress"), fallback=state.get("progress"))
        if "progress_label" in turn_payload:
            progress_label = str(turn_payload.get("progress_label") or "").strip()
            state["progress_label"] = progress_label or None
        if "active_job_id" in turn_payload:
            active_job_id = str(turn_payload.get("active_job_id") or "").strip()
            state["active_job_id"] = active_job_id or None
        state["updated_at"] = now_iso()
        self.inspiration_repo.upsert_state(state)
        reply_text = str(turn_payload.get("reply") or "").strip() or "Agent 已处理当前请求。"
        self._append_message(
            session_id=session_id,
            role="assistant",
            content=reply_text,
            stage=state["stage"],
            attachments=[],
            options=normalized_options,
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
            "dynamic_stage_label": state.get("progress_label"),
            "trace": [],
        }

    def _resolve_selected_style_profile(
        self,
        *,
        action: str | None,
        selected_items: list[str],
    ) -> dict[str, Any]:
        if action != "use_style_profile":
            return {}
        style_id = selected_items[0].strip() if selected_items else ""
        if not style_id:
            return {}
        profile = self.style_repo.get(style_id)
        if not profile:
            return {}
        return {
            "id": profile["id"],
            "name": profile.get("name"),
            "style_payload": self.style_service._normalize_style_payload(profile.get("style_payload") or {}),
        }

    def _normalize_agent_options(self, options: Any) -> dict[str, Any] | None:
        if options is None:
            return None
        items = options.get("items") if isinstance(options, dict) else options
        if not isinstance(items, list):
            raise DomainError(code="E-1099", message="Agent 返回的选项结构不合法", status_code=500)
        normalized_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise DomainError(code="E-1099", message="Agent 选项缺少结构化字段", status_code=500)
            label = str(item.get("label") or "").strip()
            if not label:
                continue
            action_hint = item.get("action_hint")
            normalized_items.append(
                {
                    "label": label,
                    "action_hint": str(action_hint).strip() if isinstance(action_hint, str) and action_hint.strip() else None,
                }
            )
        return {"items": normalized_items} if normalized_items else None

    def _normalize_progress_value(self, value: Any, *, fallback: Any = None) -> int | None:
        if isinstance(value, int):
            if 0 <= value <= 100:
                return value
            raise DomainError(code="E-1099", message="Agent 返回的进度值超出范围", status_code=500)
        if isinstance(fallback, int) and 0 <= fallback <= 100:
            return fallback
        return None

    async def _prepare_message_context(
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

        self._apply_action_state_updates(
            state=state,
            action=normalized_action,
            text=normalized_text,
            selected_items=normalized_items,
        )

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
        return {
            "session": session,
            "state": state,
            "normalized_text": normalized_text,
            "normalized_items": normalized_items,
            "normalized_action": normalized_action,
            "attachments": attachments,
            "defer_for_transcript": bool(videos and not normalized_text and not normalized_items and not images),
        }

    def _format_sse_event(self, event_type: str, data: Any) -> str:
        return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    def _sanitize_stream_error_payload(self, data: Any) -> dict[str, Any]:
        payload = data if isinstance(data, dict) else {}
        code = str(payload.get("code") or "E-1099")
        raw_message = str(payload.get("message") or "").strip()
        cleaned_message = self._sanitize_stream_error_message(raw_message)
        return {"code": code, "message": cleaned_message}

    def _sanitize_stream_error_message(self, message: str) -> str:
        normalized = " ".join(str(message or "").split())
        lowered = normalized.lower()
        if not normalized:
            return "Agent 执行异常"
        if any(marker in lowered for marker in ("<!doctype", "<html", "cloudflare", "bad gateway", "gateway", "502", "503", "504")):
            return "模型服务暂时不可用，请稍后重试"
        if len(normalized) > 240:
            return "模型服务暂时不可用，请稍后重试"
        return normalized

    def _apply_action_state_updates(
        self,
        *,
        state: dict[str, Any],
        action: str | None,
        text: str,
        selected_items: list[str],
    ) -> None:
        image_count = self._extract_image_count_from_action(action)
        if image_count is None:
            image_count = self._extract_image_count_from_inputs(text=text, selected_items=selected_items)
        if image_count is None:
            return
        state["image_count"] = image_count
        if str(state.get("style_prompt") or "").strip():
            state["prompt_confirmable"] = True
        state["locked"] = False
        state["updated_at"] = now_iso()
        self.inspiration_repo.upsert_state(state)

    def _extract_image_count_from_action(self, action: str | None) -> int | None:
        if not action:
            return None
        matched = self._IMAGE_COUNT_ACTION_PATTERN.fullmatch(action)
        if not matched:
            return None
        image_count = int(matched.group(1))
        if not 1 <= image_count <= 10:
            return None
        return image_count

    def _extract_image_count_from_inputs(self, *, text: str, selected_items: list[str]) -> int | None:
        candidates = [text, *selected_items]
        for candidate in candidates:
            image_count = self._extract_image_count_from_text(candidate)
            if image_count is not None:
                return image_count
        return None

    def _extract_image_count_from_text(self, raw_text: str) -> int | None:
        normalized = " ".join(str(raw_text or "").split())
        if not normalized:
            return None
        matched = self._TEXTUAL_IMAGE_COUNT_PATTERN.search(normalized)
        if not matched:
            return None
        return self._normalize_image_count_literal(matched.group(1))

    def _normalize_image_count_literal(self, raw_value: str) -> int | None:
        text = str(raw_value or "").strip()
        if not text:
            return None
        if text.isdigit():
            value = int(text)
            return value if 1 <= value <= 10 else None
        value = self._CHINESE_IMAGE_COUNT_MAP.get(text)
        if value is None:
            return None
        return value if 1 <= value <= 10 else None

    def _normalize_locked_state(
        self,
        *,
        stage: str,
        requested_locked: bool,
        state: dict[str, Any],
        turn_payload: dict[str, Any],
    ) -> bool:
        if not requested_locked:
            return False
        normalized_stage = str(stage or "").strip().lower()
        early_stages = {
            "initial_understanding",
            "painting_style",
            "background_decor",
            "color_mood",
            "image_count",
            "count_confirmation_required",
            "style_ready",
            "asset_ready",
            "prompt_ready",
            "prompt_confirmed",
            "briefing",
            "style_aligned",
        }
        if normalized_stage in early_stages:
            return False
        allocation_plan = turn_payload.get("allocation_plan")
        if isinstance(allocation_plan, list) and allocation_plan:
            return True
        active_job_id = str(turn_payload.get("active_job_id") or state.get("active_job_id") or "").strip()
        if active_job_id:
            return True
        return requested_locked

    def suggest_painting_style(
        self,
        *,
        session_id: str,
        stage: str,
        user_reply: str,
        selected_items: list[str],
    ) -> dict[str, Any]:
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

    def recommend_city_content_combos(self, *, session_id: str, limit: int = 2) -> dict[str, Any]:
        return super().recommend_city_content_combos(session_id=session_id, limit=limit)

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

    def generate_images(self, *, session_id: str, draft_state: dict[str, Any] | None = None) -> dict[str, Any]:
        session = self.session_repo.get(session_id)
        if not session:
            raise not_found("会话", session_id)
        state = self._ensure_state(session_id)
        if isinstance(draft_state, dict) and draft_state:
            for key in ("allocation_plan", "style_prompt", "image_count", "style_payload", "draft_style_id"):
                value = draft_state.get(key)
                if key == "allocation_plan" and isinstance(value, list) and value:
                    state["allocation_plan"] = self._mark_allocation_plan_confirmed(value)
                elif key == "style_payload" and isinstance(value, dict):
                    state["style_payload"] = self.style_service._normalize_style_payload(value)
                elif key in draft_state and value is not None:
                    state[key] = value
            state["updated_at"] = now_iso(); self.inspiration_repo.upsert_state(state); state = self._ensure_state(session_id)
        if not self.generation_worker:
            raise DomainError(code="E-1099", message="生成 Worker 不可用", status_code=500)
        existing_job_id = str(state.get("active_job_id") or "").strip()
        if existing_job_id:
            existing_job = self.generation_worker.job_repo.get(existing_job_id)
            if existing_job and existing_job.get("status") not in {"success", "partial_success", "failed", "canceled"}:
                return {
                    "job_id": existing_job_id,
                    "status": existing_job["status"],
                    "already_running": True,
                }
        allocation_plan = state.get("allocation_plan")
        if isinstance(allocation_plan, list) and allocation_plan:
            allocation_plan = self._mark_allocation_plan_confirmed(allocation_plan)
            state["allocation_plan"] = allocation_plan
        if not isinstance(allocation_plan, list) or not allocation_plan:
            raise DomainError(code="E-1099", message="当前草案还没有可生成的分图规划", status_code=400)
        style_profile_id = self._ensure_draft_style_profile(session, state)
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
        state["active_job_id"] = job["id"]
        state["updated_at"] = now_iso()
        self.inspiration_repo.upsert_state(state)
        return {"job_id": job["id"], "status": "queued"}

    def reset_progress(self, *, session_id: str, reset_to: str) -> dict[str, Any]:
        session = self.session_repo.get(session_id)
        if not session:
            raise not_found("会话", session_id)
        state = self._ensure_state(session_id)
        transcript_seen_ids = list(state.get("transcript_seen_ids") or [])
        base_style_payload = self.style_service._normalize_style_payload({})
        if reset_to == "style":
            state["stage"] = "style_reopened"
            state["style_payload"] = base_style_payload
            state["style_prompt"] = ""
            state["asset_candidates"] = {}
            state["allocation_plan"] = []
            state["draft_style_id"] = None
            state["locked"] = False
            state["progress"] = 20
            state["progress_label"] = "重新梳理风格"
            state["active_job_id"] = None
        elif reset_to == "prompt":
            state["stage"] = "prompt_reopened"
            state["style_prompt"] = ""
            state["asset_candidates"] = {}
            state["allocation_plan"] = []
            state["locked"] = False
            state["progress"] = 45
            state["progress_label"] = "重新整理提示词"
            state["active_job_id"] = None
        elif reset_to == "assets":
            state["stage"] = "assets_reopened"
            state["asset_candidates"] = {}
            state["allocation_plan"] = []
            state["locked"] = False
            state["progress"] = 55
            state["progress_label"] = "重新整理素材"
            state["active_job_id"] = None
        elif reset_to == "allocation":
            state["stage"] = "prompt_confirmed"
            state["allocation_plan"] = []
            state["locked"] = False
            state["progress"] = 60
            state["progress_label"] = "重新分图"
            state["active_job_id"] = None
        elif reset_to == "all":
            state.update(
                {
                    "stage": "initial_understanding",
                    "style_stage": "painting_style",
                    "locked": False,
                    "image_count": None,
                    "style_prompt": "",
                    "style_payload": base_style_payload,
                    "asset_candidates": {},
                    "allocation_plan": [],
                    "draft_style_id": None,
                    "requirement_ready": True,
                    "prompt_confirmable": False,
                    "progress": 10,
                    "progress_label": "初始了解",
                    "active_job_id": None,
                }
            )
        else:
            raise DomainError(code="E-1099", message="不支持的回滚阶段", status_code=400)
        state["transcript_seen_ids"] = transcript_seen_ids
        state["updated_at"] = now_iso()
        self.inspiration_repo.upsert_state(state)
        return {
            "stage": state["stage"],
            "locked": state["locked"],
            "style_payload": state.get("style_payload"),
            "style_prompt": state.get("style_prompt"),
            "asset_candidates": state.get("asset_candidates"),
            "allocation_plan": state.get("allocation_plan"),
            "draft_style_id": state.get("draft_style_id"),
            "progress": state.get("progress"),
            "progress_label": state.get("progress_label"),
            "active_job_id": state.get("active_job_id"),
        }

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
        if videos:
            self.asset_service.ensure_video_upload_ready(session_id)
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

