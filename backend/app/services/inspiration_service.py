
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

WELCOME_MESSAGE = "欢迎来到 Savory Canvas！把你的灵感发给我吧，文字、图片和视频都可以，我会帮你整理成可生成的创作方案。"
LOCKED_HINT = "已确定当前风格与资产，可开始生成。是否保存风格参数和提示词？"
ASSET_CONFIRM_HINT = "已确认风格提示词。下面是每张图重点内容建议，请按你的想法调整后再确认锁定。"
STYLE_REQUIREMENT_HINT = (
    "已应用该风格。为了生成更贴合的提示词，请先补充你的创作需求："
    "例如城市/地区、核心景点或美食、想突出哪些内容，以及计划生成几张图。"
    "你也可以继续上传图片或视频作为参考。"
)
STYLE_REQUIREMENT_SYSTEM_PROMPT = (
    "你是 Savory Canvas 的资深创意策划助手。"
    "你的目标是把用户零散想法收敛成可执行的创作方案。"
    "请先复述已确定信息，再只追问缺失信息。"
    "优先补齐：生成张数、地点、景点、美食、画面重点、叙事结构。"
    "不要模板化套话，不要一次抛出过多问题。"
    "请输出 2-4 句自然中文对话，不要输出 JSON。"
)
STYLE_PROMPT_SYSTEM_PROMPT = (
    "你是资深视觉创意总监。"
    "请根据用户需求与风格参数，输出可直接用于生图的高质量中文母提示词。"
    "要求："
    "1) 严格围绕用户明确给出的地点、景点、美食，不得擅自替换主题资产；"
    "2) 若有图片输入，请把图片同时作为风格与内容线索综合理解；"
    "3) 画面描述需包含主体、构图、镜头距离、光线、色彩、材质细节、氛围、版式约束；"
    "4) 禁止出现参数标签、解释文本、JSON、Markdown。"
    "当张数大于 1 时，必须按张数输出多段提示词，每段都以“生成一张”开头，且每段聚焦不同图。"
    "禁止使用“生成两张/生成三张/一次生成多张”等合并表达。"
)
STYLE_PROMPT_RETRY_SYSTEM_PROMPT = (
    "请重写为更专业、更可执行的中文母提示词。"
    "必须保留用户明确给出的地点、景点、美食，不得替换。"
    "若有图片输入，请结合图片与文本理解需求。"
    "每段提示词都要具体到可直接生图，不要空泛词。"
    "禁止输出参数清单、JSON、Markdown、解释文本。"
    "如果目标张数大于 1，必须输出对应数量的分图提示词，每段以“生成一张”开头。"
)
PROMPT_READINESS_SYSTEM_PROMPT = (
    "你是提示词质检助手。"
    "请判断当前信息是否足够进入“资产确认”阶段。"
    "若缺少关键要素（张数、地点、核心景点/美食、画面重点）则判定为 REVISE。"
    "仅当信息足以稳定生成分图提示词时才判定 READY。"
    "如果用户明确要求“先继续聊”，也判定 REVISE。"
    "只允许输出 READY 或 REVISE，不要输出其他内容。"
)
IMAGE_COUNT_EXTRACT_SYSTEM_PROMPT = (
    "你是参数提取助手。请从用户输入中识别本次要生成的图片张数。"
    "只输出严格 JSON：{\"image_count\": 1}。"
    "如果用户没有明确张数，输出 {\"image_count\": null}。"
    "image_count 仅允许 1-10 的整数。"
)
VISION_ERROR_MESSAGE = "当前模型不支持图片解析，请切换为视觉模型后重试。"
ASSET_EXTRACT_SYSTEM_PROMPT = (
    "你是资产提取助手。请从对话中提取本次创作资产，输出严格 JSON："
    '{"locations":[""],"scenes":[""],"foods":[""],"keywords":[""],"confidence":0.0}。'
    "要求："
    "1) locations 只放地点（城市/区域）；"
    "2) scenes 只放景点地标；"
    "3) foods 只放食物饮品；"
    "4) keywords 仅保留与地点/景点/食物强相关词；"
    "5) 去重并过滤空值；"
    "6) 不要输出风格词、摄影词、绘画词；"
    "7) 只输出 JSON，不要 Markdown，不要解释。"
)
IMAGE_ASSET_EXTRACT_SYSTEM_PROMPT = (
    "你是图片资产解析助手。请仅根据输入图片提取本次创作资产，输出严格 JSON："
    '{"locations":[""],"scenes":[""],"foods":[""],"keywords":[""],"confidence":0.0}。'
    "要求："
    "1) locations 提取地点（城市/区域）；"
    "2) scenes 提取景点地标；"
    "3) foods 提取食物饮品；"
    "4) keywords 仅保留与地点/景点/食物相关词；"
    "5) 不确定时降低 confidence，不要臆造；"
    "6) 没有就返回空数组；"
    "7) 只输出 JSON，不要 Markdown，不要解释。"
)
ALLOCATION_PLAN_SYSTEM_PROMPT = (
    "你是分图策划助手。请基于用户需求与已提取信息，输出逐图安排的严格 JSON："
    '{"items":[{"slot_index":1,"focus_title":"","focus_description":"","locations":[],"scenes":[],"foods":[],"keywords":[],"source_asset_ids":[]}]}。'
    "要求："
    "1) items 数量必须等于目标张数；"
    "2) 每条必须只描述一张图，表达具体可执行；"
    "3) 用户若明确指定分配，必须严格遵循；"
    "4) 用户说“随便/你来定”时，按“先主后辅”分配：第1张总览，其余按主题拆分；"
    "5) 不得引入用户未提及且与任务无关的地点/景点/食物实体；"
    "6) source_asset_ids 必须从可用素材 ID 中选择，且每条至少 1 个；"
    "7) 只输出 JSON，不要 Markdown，不要解释。"
)
PROMPT_ACTION_OPTIONS = {"title": "请选择下一步", "items": ["确认提示词"], "max": 1}

logger = logging.getLogger(__name__)


class InspirationService:
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
        should_detect_image_count = any(item.strip().replace("张", "").isdigit() for item in selected_items) or (
            not isinstance(state.get("image_count"), int) and bool(user_text.strip())
        )
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
            state["asset_candidates"] = self._extract_asset_candidates(session["id"], confirm_feedback or user_text)
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
            revised = self._extract_asset_candidates(session["id"], user_text)
            state["asset_candidates"] = self._merge_asset_candidates(state.get("asset_candidates"), revised)
        elif user_text:
            state["asset_candidates"] = self._extract_asset_candidates(session["id"], user_text)

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
    ) -> dict[str, Any]:
        attachment = {
            "id": asset_id,
            "asset_id": asset_id,
            "type": attachment_type,
            "name": name,
            "preview_url": preview_url,
            "status": status,
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
        for attempt in range(2):
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
                if attempt == 1 or not retryable:
                    break
        raise DomainError(
            code="E-1004",
            message="模型服务连接失败，请稍后重试",
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

    def _resolve_prompt_action_options(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        user_feedback: str,
        prompt_text: str,
    ) -> dict[str, Any] | None:
        if not bool(state.get("requirement_ready")):
            return None
        if self._assess_prompt_readiness(session, state, user_feedback, prompt_text):
            return dict(PROMPT_ACTION_OPTIONS)
        return None

    def _assess_prompt_readiness(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        user_feedback: str,
        prompt_text: str,
    ) -> bool:
        style_payload = self._build_style_payload(state)
        style_text = self._format_style_payload_text(style_payload)
        user_prompt = (
            f"图片数量：{state.get('image_count') or 0}。\n"
            f"风格参数：{style_text}。\n"
            f"本轮用户补充：{user_feedback or '无'}。\n"
            f"当前母提示词：{prompt_text or '无'}。\n"
            "请判断是否可进入资产确认。"
        )
        decision = self._call_text_model_with_retry(
            session_id=session["id"],
            system_prompt=PROMPT_READINESS_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            strict_json=False,
        ).strip().upper()
        if "READY" in decision and "REVISE" not in decision:
            return True
        if "REVISE" in decision:
            return False
        raise DomainError(code="E-1004", message="提示词确认判断失败，请稍后重试", status_code=503)

    def _upsert_draft_style(self, session: dict[str, Any], state: dict[str, Any]) -> str:
        payload = self._build_style_payload(state)
        now = now_iso()
        draft_style_id = state.get("draft_style_id")
        if draft_style_id and self.style_repo.get(draft_style_id):
            self.style_repo.update_payload(draft_style_id, payload, now)
            return draft_style_id
        created = self.style_repo.create(
            {
                "id": new_id(),
                "session_id": session["id"],
                "name": "灵感草稿",
                "style_payload": payload,
                "is_builtin": False,
                "created_at": now,
                "updated_at": now,
            }
        )
        return created["id"]

    def _create_saved_style(self, session: dict[str, Any], state: dict[str, Any]) -> None:
        now = now_iso()
        self.style_repo.create(
            {
                "id": new_id(),
                "session_id": session["id"],
                "name": f"灵感风格-{now[11:19].replace(':', '')}",
                "style_payload": self._build_style_payload(state),
                "is_builtin": False,
                "created_at": now,
                "updated_at": now,
            }
        )

    def _build_style_requirement_reply(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        profile: dict[str, Any],
    ) -> str:
        style_payload = self._build_style_payload(state)
        style_text = self._format_style_payload_text(style_payload)
        recent_user_context = self._collect_recent_user_context(session["id"], limit=6)
        user_prompt = (
            f"当前风格：{profile['name']}。\n"
            f"风格参数：{style_text}。\n"
            f"最近用户上下文：{recent_user_context or '无'}。\n"
            "请给出引导式回复，帮助用户补齐可执行的创作需求。"
        )
        model_reply = self._call_text_model_with_retry(
            session_id=session["id"],
            system_prompt=STYLE_REQUIREMENT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            strict_json=False,
        ).strip()
        if model_reply:
            return model_reply
        raise DomainError(code="E-1004", message="模型输出为空，请稍后重试", status_code=503)

    def _build_collecting_requirement_reply(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        user_text: str,
    ) -> str:
        style_payload = self._build_style_payload(state)
        style_text = self._format_style_payload_text(style_payload)
        recent_user_context = self._collect_recent_user_context(session["id"], limit=6)
        user_prompt = (
            f"已确定风格参数：{style_text}。\n"
            f"用户本轮输入：{user_text or '无'}。\n"
            f"最近用户上下文：{recent_user_context or '无'}。\n"
            "请继续引导用户补齐可执行需求，重点确认：生成张数、地点、景点、美食与画面重点。"
        )
        model_reply = self._call_text_model_with_retry(
            session_id=session["id"],
            system_prompt=STYLE_REQUIREMENT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            strict_json=False,
        ).strip()
        if model_reply:
            return model_reply
        raise DomainError(code="E-1004", message="模型输出为空，请稍后重试", status_code=503)

    def _build_allocation_plan(
        self,
        *,
        session: dict[str, Any],
        state: dict[str, Any],
        user_hint: str,
    ) -> list[dict[str, Any]]:
        image_count_raw = state.get("image_count")
        image_count = int(image_count_raw) if isinstance(image_count_raw, int) else 0
        if image_count <= 0:
            raise DomainError(code="E-1004", message="请先确认生成张数后再分配每张图重点内容", status_code=400)
        assets = self.asset_repo.list_by_session(session["id"])
        source_asset_ids = [str(asset.get("id")).strip() for asset in assets if str(asset.get("id") or "").strip()]
        if not source_asset_ids:
            raise DomainError(code="E-1003", message="素材不足，无法完成分图确认", status_code=400)
        candidates = state.get("asset_candidates") if isinstance(state.get("asset_candidates"), dict) else {}
        foods = "、".join(candidates.get("foods") or []) or "无"
        scenes = "、".join(candidates.get("scenes") or []) or "无"
        keywords = "、".join(candidates.get("keywords") or []) or "无"
        style_text = self._format_style_payload_text(self._build_style_payload(state))
        recent_context = self._collect_recent_user_context(session["id"], limit=8) or "无"
        user_prompt = (
            f"目标张数：{image_count}\n"
            f"风格参数：{style_text}\n"
            f"已提取美食：{foods}\n"
            f"已提取景点：{scenes}\n"
            f"已提取关键词：{keywords}\n"
            f"最近用户上下文：{recent_context}\n"
            f"本轮补充：{user_hint or '无'}\n"
            f"可用 source_asset_ids：{', '.join(source_asset_ids)}\n"
            "请输出严格 JSON。"
        )
        session_image_urls = self._collect_session_image_asset_urls(session["id"])
        if session_image_urls:
            model_text = self._call_vision_model_with_retry(
                session_id=session["id"],
                system_prompt=ALLOCATION_PLAN_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                image_urls=session_image_urls,
                strict_json=True,
            )
        else:
            model_text = self._call_text_model_with_retry(
                session_id=session["id"],
                system_prompt=ALLOCATION_PLAN_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                strict_json=True,
            )
        return self._parse_allocation_plan_payload(
            model_text=model_text,
            image_count=image_count,
            source_asset_ids=source_asset_ids,
            asset_candidates=candidates,
        )

    def _parse_allocation_plan_payload(
        self,
        *,
        model_text: str,
        image_count: int,
        source_asset_ids: list[str],
        asset_candidates: dict[str, Any],
    ) -> list[dict[str, Any]]:
        text = model_text.strip()
        if text.startswith("```"):
            lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()
        start_index = text.find("{")
        end_index = text.rfind("}")
        if start_index == -1 or end_index == -1 or end_index < start_index:
            raise DomainError(code="E-1004", message="分图确认失败：模型返回格式异常，请重试", status_code=503)
        try:
            payload = json.loads(text[start_index : end_index + 1])
        except json.JSONDecodeError as error:
            raise DomainError(code="E-1004", message="分图确认失败：模型返回格式异常，请重试", status_code=503) from error
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or len(items) < image_count:
            raise DomainError(code="E-1004", message="分图确认失败：模型返回的分图数量不足，请重试", status_code=503)
        valid_source_ids = {asset_id for asset_id in source_asset_ids if asset_id}
        fallback_source_ids = [asset_id for asset_id in source_asset_ids if asset_id][:3]
        fallback_locations = self._normalize_asset_list(asset_candidates.get("locations")) or []
        fallback_scenes = self._normalize_asset_list(asset_candidates.get("scenes")) or []
        fallback_foods = self._normalize_asset_list(asset_candidates.get("foods")) or []
        fallback_keywords = self._normalize_asset_list(asset_candidates.get("keywords")) or []
        normalized_items: list[dict[str, Any]] = []
        for index, raw_item in enumerate(items[:image_count], start=1):
            item = raw_item if isinstance(raw_item, dict) else {}
            focus_description = str(item.get("focus_description") or "").strip()
            if not focus_description:
                raise DomainError(code="E-1004", message="分图确认失败：缺少每张图重点内容描述，请重试", status_code=503)
            slot_index_raw = item.get("slot_index")
            slot_index = slot_index_raw if isinstance(slot_index_raw, int) and slot_index_raw > 0 else index
            source_ids = self._normalize_source_asset_ids(item.get("source_asset_ids"), valid_source_ids)
            if not source_ids:
                source_ids = list(fallback_source_ids)
            if not source_ids:
                raise DomainError(code="E-1004", message="分图确认失败：缺少可追溯素材来源，请重试", status_code=503)
            locations = self._normalize_asset_list(item.get("locations")) or fallback_locations
            scenes = self._normalize_asset_list(item.get("scenes")) or fallback_scenes
            foods = self._normalize_asset_list(item.get("foods")) or fallback_foods
            keywords = self._normalize_asset_list(item.get("keywords")) or fallback_keywords
            normalized_items.append(
                {
                    "slot_index": slot_index,
                    "focus_title": str(item.get("focus_title") or f"第{index}张重点").strip() or f"第{index}张重点",
                    "focus_description": focus_description,
                    "locations": locations[:8],
                    "scenes": scenes[:10],
                    "foods": foods[:10],
                    "keywords": keywords[:15],
                    "source_asset_ids": source_ids[:5],
                    "confirmed": False,
                }
            )
        normalized_items.sort(key=lambda entry: int(entry.get("slot_index") or 0))
        for index, item in enumerate(normalized_items, start=1):
            item["slot_index"] = index
        return normalized_items

    def _normalize_source_asset_ids(self, values: Any, valid_source_ids: set[str]) -> list[str]:
        if not isinstance(values, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            if not text or text in seen or text not in valid_source_ids:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized

    def _build_asset_confirm_reply(self, state: dict[str, Any]) -> str:
        allocation_plan = state.get("allocation_plan") if isinstance(state.get("allocation_plan"), list) else []
        if not allocation_plan:
            return f"{ASSET_CONFIRM_HINT}\n\n暂未生成分图建议，请补充需求后继续。"
        sections = [ASSET_CONFIRM_HINT]
        for plan_item in allocation_plan:
            slot_index = int(plan_item.get("slot_index") or 0)
            focus_title = str(plan_item.get("focus_title") or "").strip()
            focus_description = str(plan_item.get("focus_description") or "").strip()
            locations = "、".join(plan_item.get("locations") or []) or "无"
            scenes = "、".join(plan_item.get("scenes") or []) or "无"
            foods = "、".join(plan_item.get("foods") or []) or "无"
            line_title = f"第{slot_index}张" if slot_index > 0 else "分图建议"
            if focus_title:
                line_title = f"{line_title}（{focus_title}）"
            sections.append(
                f"{line_title}：{focus_description}\n"
                f"- 地点：{locations}\n"
                f"- 景点：{scenes}\n"
                f"- 美食：{foods}"
            )
        sections.append("如果你有指定分配，直接回复“第几张做什么”；如果你说“随便”，我会按主题自动分配。")
        return "\n\n".join(sections)

    def _mark_allocation_plan_confirmed(self, allocation_plan: Any) -> list[dict[str, Any]]:
        if not isinstance(allocation_plan, list):
            return []
        confirmed_items: list[dict[str, Any]] = []
        for item in allocation_plan:
            if not isinstance(item, dict):
                continue
            confirmed_items.append({**item, "confirmed": True})
        return confirmed_items

    def _collect_recent_user_context(self, session_id: str, limit: int) -> str:
        messages = self.inspiration_repo.list_messages(session_id)
        if not messages:
            return ""
        user_messages = [message for message in messages if message.get("role") == "user"]
        if not user_messages:
            return ""
        segments: list[str] = []
        total_length = 0
        for message in user_messages[-limit:]:
            content = str(message.get("content") or "").strip()
            if content:
                if content.startswith("已选择风格："):
                    continue
                normalized = content.replace("\n", " ").strip()
                if len(normalized) > 180:
                    normalized = f"{normalized[:180]}..."
                next_length = total_length + len(normalized)
                if next_length > 520:
                    break
                segments.append(normalized)
                total_length = next_length
        return " | ".join(segments)

    def _build_style_payload(self, state: dict[str, Any]) -> dict[str, Any]:
        payload = dict(state.get("style_payload") or {})
        result = self.style_service._normalize_style_payload(payload)
        if state.get("style_prompt"):
            result["style_prompt"] = str(state["style_prompt"]).strip()
        allocation_plan = state.get("allocation_plan")
        if isinstance(allocation_plan, list) and allocation_plan:
            result["allocation_plan"] = allocation_plan
        return result

    def _format_style_payload_text(self, style_payload: dict[str, Any]) -> str:
        prompt_example = str(style_payload.get("prompt_example") or "").strip()
        if len(prompt_example) > 160:
            prompt_example = f"{prompt_example[:160]}..."
        segments = [
            f"绘画风格：{style_payload.get('painting_style', '手绘插画')}",
            f"色彩情绪：{style_payload.get('color_mood', '温暖治愈')}",
            f"提示词样例：{prompt_example or '请保持统一风格'}",
        ]
        keywords = style_payload.get("extra_keywords") or []
        if isinstance(keywords, list) and keywords:
            normalized_keywords = [str(item).strip() for item in keywords if str(item).strip()]
            if len(normalized_keywords) > 12:
                normalized_keywords = normalized_keywords[:12]
            segments.append("关键词：" + "、".join(normalized_keywords))
        return "；".join(segments)

    def _ensure_state(self, session_id: str) -> dict[str, Any]:
        state = self.inspiration_repo.get_state(session_id)
        if state:
            return state
        return self.inspiration_repo.upsert_state(
            {
                "session_id": session_id,
                "stage": "style_collecting",
                "style_stage": "painting_style",
                "locked": False,
                "image_count": None,
                "style_prompt": "",
                "style_payload": self.style_service._normalize_style_payload({}),
                "asset_candidates": {},
                "allocation_plan": [],
                "draft_style_id": None,
                "requirement_ready": True,
                "prompt_confirmable": False,
                "transcript_seen_ids": [],
                "updated_at": now_iso(),
            }
        )

    def _ensure_welcome_message(self, session_id: str, state: dict[str, Any]) -> None:
        if self.inspiration_repo.list_messages(session_id):
            return
        self._append_message(
            session_id=session_id,
            role="assistant",
            content=WELCOME_MESSAGE,
            stage=state["stage"],
            attachments=[],
            options=None,
            fallback_used=False,
        )

    def _append_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        stage: str,
        attachments: list[dict[str, Any]],
        options: dict[str, Any] | None,
        fallback_used: bool,
        asset_candidates: dict[str, Any] | None = None,
        style_context: dict[str, Any] | None = None,
    ) -> None:
        self.inspiration_repo.add_message(
            {
                "id": new_id(),
                "session_id": session_id,
                "role": role,
                "content": content,
                "attachments": attachments,
                "options": options,
                "asset_candidates": asset_candidates,
                "style_context": style_context,
                "stage": stage,
                "fallback_used": fallback_used,
                "created_at": now_iso(),
            }
        )

    def _ingest_ready_transcripts(self, session_id: str, state: dict[str, Any]) -> None:
        seen_ids = set(state.get("transcript_seen_ids") or [])
        changed = False
        for asset in self.asset_repo.list_by_session(session_id):
            if asset.get("asset_type") != "transcript" or asset.get("status") != "ready":
                continue
            transcript_id = asset["id"]
            if transcript_id in seen_ids:
                continue
            changed = True
            seen_ids.add(transcript_id)
            self._append_message(
                session_id=session_id,
                role="user",
                content=f"视频转写补充：{asset.get('content') or ''}",
                stage=state["stage"],
                attachments=[
                    self._build_attachment(
                        transcript_id,
                        "transcript",
                        "视频转写",
                        asset.get("file_path"),
                        "ready",
                    )
                ],
                options=None,
                fallback_used=False,
            )
        if changed:
            state["transcript_seen_ids"] = sorted(seen_ids)
            state["updated_at"] = now_iso()
            self.inspiration_repo.upsert_state(state)

    def _merge_style_payload(self, state: dict[str, Any], style_stage: str, selected_items: list[str]) -> None:
        if not selected_items:
            return
        payload = self._build_style_payload(state)
        joined = "、".join(selected_items)
        if style_stage == "painting_style":
            payload["painting_style"] = joined
        elif style_stage == "color_mood":
            payload["color_mood"] = joined
        elif style_stage == "background_decor":
            payload["prompt_example"] = f"{payload.get('prompt_example', '')}；背景偏好：{joined}".strip("；")
            payload["extra_keywords"] = self._merge_keyword_values(payload.get("extra_keywords"), selected_items)
        elif style_stage == "image_count":
            image_count = self._extract_image_count(selected_items, "")
            if image_count is not None:
                state["image_count"] = image_count
        state["style_payload"] = payload

    def _extract_image_count(
        self,
        selected_items: list[str],
        user_text: str,
        *,
        session_id: str | None = None,
    ) -> int | None:
        if selected_items:
            candidate = selected_items[0].strip().replace("张", "")
            if candidate.isdigit():
                image_count = int(candidate)
                if 1 <= image_count <= 10:
                    return image_count
        normalized_text = user_text.strip()
        if not normalized_text or not session_id:
            return None
        if not any(char.isdigit() for char in normalized_text) and not any(
            char in "一二三四五六七八九十两" for char in normalized_text
        ):
            return None
        model_text = self._call_text_model_with_retry(
            session_id=session_id,
            system_prompt=IMAGE_COUNT_EXTRACT_SYSTEM_PROMPT,
            user_prompt=f"用户输入：{normalized_text}",
            strict_json=True,
        )
        payload = self._parse_image_count_payload(model_text)
        image_count = payload.get("image_count")
        if isinstance(image_count, int) and 1 <= image_count <= 10:
            return image_count
        if isinstance(image_count, str):
            candidate = image_count.strip().replace("张", "")
            if candidate.isdigit():
                normalized = int(candidate)
                if 1 <= normalized <= 10:
                    return normalized
        return None

    def _parse_image_count_payload(self, model_text: str) -> dict[str, Any]:
        text = model_text.strip()
        if text.startswith("```"):
            lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()
        start_index = text.find("{")
        end_index = text.rfind("}")
        if start_index == -1 or end_index == -1 or end_index < start_index:
            raise DomainError(code="E-1004", message="模型输出格式异常，请重试", status_code=503)
        json_text = text[start_index : end_index + 1]
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as error:
            raise DomainError(code="E-1004", message="模型输出格式异常，请重试", status_code=503) from error
        if not isinstance(payload, dict):
            raise DomainError(code="E-1004", message="模型输出格式异常，请重试", status_code=503)
        return payload

    def _ensure_image_count_options(self, options: dict[str, Any] | None) -> dict[str, Any]:
        if isinstance(options, dict):
            title = options.get("title")
            items = options.get("items")
            max_value = options.get("max")
            if isinstance(title, str) and isinstance(items, list) and items and isinstance(max_value, int) and max_value == 1:
                return options
        raise DomainError(code="E-1004", message="模型返回数量选项异常，请重试", status_code=503)

    def _normalize_selected_items(self, selected_items: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in selected_items:
            text = item.strip()
            if text:
                normalized.append(text)
        return normalized

    def _build_user_message(
        self,
        text: str,
        selected_items: list[str],
        attachments: list[dict[str, Any]],
    ) -> str:
        parts: list[str] = []
        if text:
            parts.append(text)
        if selected_items:
            parts.append("已选择：" + "、".join(selected_items))
        if not parts and attachments:
            return "已上传附件"
        if not parts:
            return "继续"
        return "；".join(parts)

    def _build_use_style_profile_user_message(
        self,
        *,
        user_text: str,
        selected_items: list[str],
        attachments: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        style_id = selected_items[0].strip() if selected_items else user_text.strip()
        if not style_id:
            return self._build_user_message(user_text, selected_items, attachments), attachments
        profile = self.style_repo.get(style_id)
        if not profile:
            return self._build_user_message(user_text, selected_items, attachments), attachments
        payload = self.style_service._normalize_style_payload(dict(profile.get("style_payload") or {}))
        lines = [
            f"已选择风格：{profile.get('name', '未命名风格')}",
            f"绘画风格：{payload.get('painting_style') or '-'}",
            f"色彩情绪：{payload.get('color_mood') or '-'}",
        ]
        keywords = payload.get("extra_keywords") or []
        if isinstance(keywords, list) and keywords:
            lines.append("风格细节关键词：" + "、".join(str(item) for item in keywords if str(item).strip()))
        merged_attachments = list(attachments)
        sample_image_asset_id = payload.get("sample_image_asset_id")
        sample_preview_url = self.style_service._resolve_sample_image_preview_url(payload)
        if isinstance(sample_preview_url, str) and sample_preview_url.strip():
            merged_attachments.append(
                self._build_attachment(
                    asset_id=sample_image_asset_id if isinstance(sample_image_asset_id, str) else f"style-sample-{profile.get('id', '')}",
                    attachment_type="image",
                    name="风格样例图",
                    preview_url=sample_preview_url,
                    status="ready",
                )
            )
        return "\n".join(lines), merged_attachments

    def _ensure_vision_capable(self) -> None:
        routing = self.model_service.require_routing()
        text_model = routing.get("text_model") or {}
        provider_id = text_model.get("provider_id")
        model_name = text_model.get("model_name")
        if not provider_id or not model_name:
            raise DomainError(code="E-1006", message="请先完成模型设置", status_code=400)

        provider = self.model_service.provider_repo.get(provider_id)
        if not provider or not provider.get("enabled"):
            raise DomainError(code="E-1006", message="文字模型提供商不可用", status_code=400)

        models = self.model_service.fetch_provider_models(provider)
        target_model = next((item for item in models if item.get("name") == model_name), None)
        if not target_model:
            raise DomainError(code="E-1006", message="文字模型不存在", status_code=400)
        if "vision" not in (target_model.get("capabilities") or []):
            raise DomainError(code="E-1010", message=VISION_ERROR_MESSAGE, status_code=400)
    def _extract_asset_candidates(self, session_id: str, user_hint: str) -> dict[str, Any]:
        assets = self.asset_repo.list_by_session(session_id)
        source_assets = [asset for asset in assets if isinstance(asset.get("id"), str)]
        source_asset_ids = [str(asset["id"]) for asset in source_assets if isinstance(asset.get("id"), str)]
        extraction_context = self._build_asset_extraction_context(session_id, source_assets, user_hint)
        extracted = self._extract_assets_with_llm(session_id, extraction_context)
        recent_user_context = self._collect_recent_user_context(session_id, limit=8)
        focus = self._infer_asset_focus(user_hint=user_hint, recent_user_context=recent_user_context)
        locations = extracted["locations"]
        scenes = self._merge_keyword_values(extracted["scenes"], locations)
        foods = extracted["foods"]
        if focus == "food_only":
            scenes = []
            keywords = self._merge_keyword_values(extracted["keywords"], locations + foods)
        elif focus == "scene_only":
            foods = []
            keywords = self._merge_keyword_values(extracted["keywords"], locations + scenes)
        else:
            keywords = self._merge_keyword_values(extracted["keywords"], locations + scenes + foods)
        confidence = extracted["confidence"]
        return {
            "foods": foods[:10],
            "scenes": scenes[:10],
            "keywords": keywords[:20],
            "source_asset_ids": source_asset_ids,
            "confidence": confidence,
        }

    def _infer_asset_focus(self, *, user_hint: str, recent_user_context: str) -> str:
        text = f"{recent_user_context} {user_hint}".lower()
        no_scene_hints = [
            "不要景点",
            "不需要景点",
            "不含景点",
            "无景点",
            "只要美食",
            "仅美食",
            "纯美食",
            "只做美食",
            "美食攻略",
        ]
        no_food_hints = [
            "不要美食",
            "不需要美食",
            "不含美食",
            "无美食",
            "只要景点",
            "仅景点",
            "纯景点",
            "只做景点",
            "景点攻略",
        ]
        if any(hint in text for hint in no_scene_hints):
            return "food_only"
        if any(hint in text for hint in no_food_hints):
            return "scene_only"
        food_keywords = [
            "美食",
            "小吃",
            "饮品",
            "菜",
            "面",
            "馍",
            "汤",
            "烤",
            "biangbiang",
            "肉夹馍",
            "羊肉泡馍",
            "冰峰",
        ]
        scene_keywords = [
            "景点",
            "景区",
            "地标",
            "路线",
            "行程",
            "打卡",
            "钟楼",
            "华清池",
            "兵马俑",
        ]
        has_food = any(keyword in text for keyword in food_keywords)
        has_scene = any(keyword in text for keyword in scene_keywords)
        if has_food and not has_scene:
            return "food_only"
        if has_scene and not has_food:
            return "scene_only"
        return "mixed"

    def _build_asset_extraction_context(
        self,
        session_id: str,
        source_assets: list[dict[str, Any]],
        user_hint: str,
    ) -> str:
        parts: list[str] = []
        if user_hint.strip():
            parts.append(f"用户本轮补充：{user_hint.strip()}")
        recent_user_context = self._collect_recent_user_context(session_id, limit=8)
        if recent_user_context:
            parts.append(f"近期用户上下文：{recent_user_context}")
        for asset in source_assets:
            asset_type = str(asset.get("asset_type") or "")
            content = str(asset.get("content") or "").strip()
            if asset_type in {"food_name", "scenic_name", "text", "transcript"} and content:
                parts.append(f"{asset_type}: {content}")
        image_urls = self._collect_image_urls_from_assets(source_assets)
        image_hint = self._build_image_semantic_hint(session_id, image_urls)
        if image_hint:
            parts.append(f"图片语义：{image_hint}")
        return "\n".join(parts)

    def _collect_session_image_asset_urls(self, session_id: str) -> list[str]:
        assets = self.asset_repo.list_by_session(session_id)
        return self._collect_image_urls_from_assets(assets)

    def _collect_image_urls_from_assets(self, assets: list[dict[str, Any]]) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for asset in assets:
            if asset.get("asset_type") != "image":
                continue
            file_path = asset.get("file_path")
            public_url = self.style_service._build_public_image_url(file_path)
            if not public_url or public_url in seen:
                continue
            seen.add(public_url)
            urls.append(public_url)
        return urls[:4]

    def _build_image_semantic_hint(self, session_id: str, image_urls: list[str]) -> str:
        if not image_urls:
            return ""
        extracted = self._extract_image_assets_with_llm(session_id, image_urls)
        terms = self._merge_keyword_values(
            [],
            extracted["locations"] + extracted["scenes"] + extracted["foods"] + extracted["keywords"],
        )
        return "、".join(terms[:20])

    def _extract_image_assets_with_llm(self, session_id: str, image_urls: list[str]) -> dict[str, Any]:
        self._ensure_vision_capable()
        response_text = self._call_vision_model_with_retry(
            session_id=session_id,
            system_prompt=IMAGE_ASSET_EXTRACT_SYSTEM_PROMPT,
            user_prompt="请根据图片识别地点、景点和食物资产，并输出 JSON。",
            image_urls=image_urls,
            strict_json=True,
        )
        payload = self._parse_asset_extraction_payload(response_text)
        locations = self._normalize_asset_list(payload.get("locations"))
        scenes = self._normalize_asset_list(payload.get("scenes"))
        foods = self._normalize_asset_list(payload.get("foods"))
        keywords = self._normalize_asset_list(payload.get("keywords"))
        confidence_raw = payload.get("confidence")
        confidence = float(confidence_raw) if isinstance(confidence_raw, (int, float)) else 0.8
        confidence = min(1.0, max(0.0, confidence))
        return {
            "locations": locations,
            "scenes": scenes,
            "foods": foods,
            "keywords": keywords,
            "confidence": round(confidence, 2),
        }

    def _call_vision_model_with_retry(
        self,
        *,
        session_id: str,
        system_prompt: str,
        user_prompt: str,
        image_urls: list[str],
        strict_json: bool,
    ) -> str:
        provider, model_name = self.style_service._resolve_text_model_provider()
        last_error: StyleFallbackError | None = None
        for attempt in range(2):
            try:
                return self.style_service._call_text_model_with_images(
                    provider,
                    model_name,
                    system_prompt,
                    user_prompt,
                    image_urls,
                    strict_json=strict_json,
                )
            except StyleFallbackError as error:
                last_error = error
                retryable = self._is_retryable_model_error(error)
                logger.warning(
                    "视觉模型调用失败: session_id=%s attempt=%s reason=%s detail=%s retryable=%s",
                    session_id,
                    attempt + 1,
                    error.reason,
                    error.detail,
                    retryable,
                )
                if attempt == 1 or not retryable:
                    break
        raise DomainError(
            code="E-1004",
            message="模型服务连接失败，请稍后重试",
            status_code=503,
            details={"reason": last_error.reason if last_error else "unknown"},
        )

    def _extract_assets_with_llm(self, session_id: str, context_text: str) -> dict[str, Any]:
        if not context_text.strip():
            raise DomainError(code="E-1004", message="缺少可用于提取资产的用户内容，请先补充需求", status_code=400)
        response_text = self._call_text_model_with_retry(
            session_id=session_id,
            system_prompt=ASSET_EXTRACT_SYSTEM_PROMPT,
            user_prompt=f"请提取资产并输出 JSON：\n{context_text}",
            strict_json=True,
        )
        payload = self._parse_asset_extraction_payload(response_text)
        locations = self._normalize_asset_list(payload.get("locations"))
        scenes = self._normalize_asset_list(payload.get("scenes"))
        foods = self._normalize_asset_list(payload.get("foods"))
        keywords = self._normalize_asset_list(payload.get("keywords"))
        confidence_raw = payload.get("confidence")
        confidence = float(confidence_raw) if isinstance(confidence_raw, (int, float)) else 0.8
        confidence = min(1.0, max(0.0, confidence))
        return {
            "locations": locations,
            "scenes": scenes,
            "foods": foods,
            "keywords": keywords,
            "confidence": round(confidence, 2),
        }

    def _parse_asset_extraction_payload(self, model_text: str) -> dict[str, Any]:
        text = model_text.strip()
        if text.startswith("```"):
            lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()
        start_index = text.find("{")
        end_index = text.rfind("}")
        if start_index == -1 or end_index == -1 or end_index < start_index:
            raise DomainError(code="E-1004", message="资产提取失败：模型返回格式异常，请重试", status_code=503)
        json_text = text[start_index : end_index + 1]
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as error:
            raise DomainError(code="E-1004", message="资产提取失败：模型返回格式异常，请重试", status_code=503) from error
        if not isinstance(payload, dict):
            raise DomainError(code="E-1004", message="资产提取失败：模型返回格式异常，请重试", status_code=503)
        return payload

    def _normalize_asset_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item).strip()
            if len(text) < 2 or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized

    def _merge_asset_candidates(self, current: Any, incoming: dict[str, Any]) -> dict[str, Any]:
        base = current if isinstance(current, dict) else {}
        foods = self._merge_keyword_values(base.get("foods"), incoming.get("foods") or [])
        scenes = self._merge_keyword_values(base.get("scenes"), incoming.get("scenes") or [])
        keywords = self._merge_keyword_values(base.get("keywords"), incoming.get("keywords") or [])
        source_asset_ids = self._merge_keyword_values(base.get("source_asset_ids"), incoming.get("source_asset_ids") or [])
        confidence = incoming.get("confidence") if isinstance(incoming.get("confidence"), (int, float)) else base.get("confidence")
        return {
            "foods": foods[:10],
            "scenes": scenes[:10],
            "keywords": keywords[:20],
            "source_asset_ids": source_asset_ids,
            "confidence": confidence,
        }

    def _merge_keyword_values(self, base_values: Any, extra_values: list[Any]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for value in list(base_values or []) + list(extra_values or []):
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
        return merged

    def _build_style_context(self, state: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
        style_profile_id = state.get("draft_style_id")
        style_name = profile.get("name") if profile else None
        style_payload = self._build_style_payload(state)
        sample_image_asset_id = style_payload.get("sample_image_asset_id")
        sample_preview_url = self.style_service._resolve_sample_image_preview_url(style_payload)
        return {
            "style_profile_id": style_profile_id,
            "style_name": style_name,
            "sample_image_asset_id": sample_image_asset_id,
            "sample_image_preview_url": sample_preview_url,
            "style_payload": style_payload,
        }

    def _build_response(self, session_id: str, state: dict[str, Any]) -> dict[str, Any]:
        messages = self.inspiration_repo.list_messages(session_id)
        self._hydrate_attachment_preview_urls(messages)
        return {
            "session_id": session_id,
            "messages": messages,
            "draft": {
                "stage": state.get("stage", "style_collecting"),
                "style_payload": self._build_style_payload(state),
                "image_count": state.get("image_count"),
                "draft_style_id": state.get("draft_style_id"),
                "allocation_plan": state.get("allocation_plan") if isinstance(state.get("allocation_plan"), list) else [],
                "locked": bool(state.get("locked")),
            },
        }

    def _hydrate_attachment_preview_urls(self, messages: list[dict[str, Any]]) -> None:
        for message in messages:
            attachments = message.get("attachments")
            if not isinstance(attachments, list):
                continue
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                if attachment.get("type") != "image":
                    continue
                preview_url = attachment.get("preview_url")
                if isinstance(preview_url, str) and preview_url.strip():
                    public_url = self.style_service._build_public_image_url(preview_url)
                    if public_url:
                        attachment["preview_url"] = public_url
                        continue
                asset_id = attachment.get("asset_id") or attachment.get("id")
                if not isinstance(asset_id, str) or not asset_id.strip():
                    continue
                asset = self.asset_repo.get(asset_id.strip())
                if not asset or asset.get("asset_type") != "image":
                    continue
                public_url = self.style_service._build_public_image_url(asset.get("file_path"))
                if public_url:
                    attachment["preview_url"] = public_url
