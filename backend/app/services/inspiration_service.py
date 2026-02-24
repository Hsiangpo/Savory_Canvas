
from __future__ import annotations

import re
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
ASSET_CONFIRM_HINT = "已确认风格提示词，请确认本次资产清单。确认后即可锁定方案并生成。"
VISION_ERROR_MESSAGE = "当前模型不支持图片解析，请切换为视觉模型后重试。"
FOOD_MARKERS = ("饭", "面", "汤", "鸡", "鸭", "鱼", "虾", "蟹", "肉", "牛", "羊", "猪", "茶", "咖啡", "甜点")
SCENE_MARKERS = ("海", "山", "寺", "街", "夜景", "日落", "晨光", "窗", "草原", "沙漠", "火山", "湖", "森林")


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
        if not normalized_text and not normalized_items and not normalized_action and not images and not videos:
            raise DomainError(code="E-1099", message="请输入内容或选择选项", status_code=400)

        if images:
            self._ensure_vision_capable()

        attachments = await self._save_attachments(
            session_id=session_id,
            text=normalized_text,
            images=images,
            image_usages=image_usages,
            videos=videos,
        )
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
            self._handle_prompt_revision(session, state, normalized_text, normalized_items, normalized_action)
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
            image_count = self._extract_image_count(selected_items, user_text)
            if image_count is None:
                state["style_stage"] = "image_count"
                state["stage"] = "style_collecting"
                reply_text = "请先确认本次需要生成的图片数量。"
                options = self._ensure_image_count_options(chat_result.get("options"))
            else:
                state["image_count"] = image_count
                state["style_stage"] = "image_count"
                state["stage"] = "prompt_revision"
                state["style_prompt"] = self._generate_style_prompt(session, state, "")
                reply_text = f"已生成风格提示词，请确认或继续优化：\n\n{state['style_prompt']}"
                options = {"title": "请选择下一步", "items": ["继续优化", "确定使用"], "max": 1}
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
    ) -> None:
        confirm_prompt = action == "confirm_prompt" or "确定使用" in selected_items
        if confirm_prompt:
            state["asset_candidates"] = self._extract_asset_candidates(session["id"], user_text)
            state["stage"] = "asset_confirming"
            state["updated_at"] = now_iso()
            self.inspiration_repo.upsert_state(state)
            self._append_message(
                session_id=session["id"],
                role="assistant",
                content=ASSET_CONFIRM_HINT,
                stage=state["stage"],
                attachments=[],
                options={"title": "请选择下一步", "items": ["确认资产并锁定", "继续调整资产"], "max": 1},
                fallback_used=False,
                asset_candidates=state.get("asset_candidates"),
                style_context=self._build_style_context(state),
            )
            return

        feedback = user_text or "请继续优化提示词细节"
        revised_prompt = self._generate_style_prompt(session, state, feedback)
        state["style_prompt"] = revised_prompt
        state["updated_at"] = now_iso()
        self.inspiration_repo.upsert_state(state)
        self._append_message(
            session_id=session["id"],
            role="assistant",
            content=f"已根据你的反馈修订提示词：\n\n{revised_prompt}",
            stage=state["stage"],
            attachments=[],
            options={"title": "请选择下一步", "items": ["继续优化", "确定使用"], "max": 1},
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
        confirm_assets = action == "confirm_assets" or "确认资产并锁定" in selected_items or "确认资产" in selected_items
        revise_assets = action == "revise_assets" or "继续调整资产" in selected_items
        if confirm_assets:
            style_profile_id = self._upsert_draft_style(session, state)
            state["locked"] = True
            state["stage"] = "locked"
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

        state["updated_at"] = now_iso()
        self.inspiration_repo.upsert_state(state)
        self._append_message(
            session_id=session["id"],
            role="assistant",
            content="已更新资产候选，请确认后锁定方案。",
            stage=state["stage"],
            attachments=[],
            options={"title": "请选择下一步", "items": ["确认资产并锁定", "继续调整资产"], "max": 1},
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
        state["draft_style_id"] = profile["id"]
        state["updated_at"] = now_iso()
        self.inspiration_repo.upsert_state(state)
        self._append_message(
            session_id=session["id"],
            role="assistant",
            content=f"已应用风格「{profile['name']}」，请确认或继续优化提示词。",
            stage=state["stage"],
            attachments=[],
            options={"title": "请选择下一步", "items": ["继续优化", "确定使用"], "max": 1},
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
        image_usages: list[str],
        videos: list[UploadFile],
    ) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []
        if text:
            text_asset = self.asset_service.create_text_asset(session_id, "text", text)
            attachments.append(self._build_attachment(text_asset["id"], "text", "文本", None, "ready"))
        for image_index, image in enumerate(images):
            usage_type = self._resolve_image_usage_type(image_usages, image_index)
            image_name = image.filename or "upload.png"
            image_suffix = Path(image_name).suffix or ".png"
            image_file_name = f"{session_id}_{new_id()}{image_suffix}"
            image_content = await image.read()
            image_path = self.storage.save_image(image_file_name, image_content)
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
                    image_path,
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
                    usage_type=None,
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
        }
        if usage_type:
            attachment["usage_type"] = usage_type
        return attachment

    def _resolve_image_usage_type(self, image_usages: list[str], image_index: int) -> str:
        usage_value = image_usages[image_index] if image_index < len(image_usages) else None
        if usage_value == "style_reference":
            return "style_reference"
        return "content_asset"

    def _generate_style_prompt(self, session: dict[str, Any], state: dict[str, Any], feedback: str) -> str:
        style_payload = self._build_style_payload(state)
        image_count = state.get("image_count") or 1
        style_text = self._format_style_payload_text(style_payload)
        prompt_prefix = (
            f"创作模式：{session.get('content_mode', 'food')}；张数：{image_count}；"
            f"风格参数：{style_text}；修订意见：{feedback or '无'}。"
        )
        fallback_prompt = f"请按如下风格创作：{prompt_prefix}"
        try:
            provider, model_name = self.style_service._resolve_text_model_provider()
            model_text = self.style_service._call_text_model(
                provider,
                model_name,
                "请生成中文图像创作提示词，输出纯文本，不要 Markdown，不要 JSON。",
                f"{prompt_prefix}请输出最终可用于图像生成的完整提示词。",
                strict_json=False,
            )
        except StyleFallbackError:
            return fallback_prompt
        except DomainError as error:
            if error.code == "E-1006":
                return fallback_prompt
            raise
        prompt_text = model_text.strip()
        if not prompt_text or (prompt_text.startswith("{") and prompt_text.endswith("}")):
            return fallback_prompt
        return prompt_text
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

    def _build_style_payload(self, state: dict[str, Any]) -> dict[str, Any]:
        payload = dict(state.get("style_payload") or {})
        result = self.style_service._normalize_style_payload(payload)
        if state.get("style_prompt"):
            result["style_prompt"] = str(state["style_prompt"]).strip()
        return result

    def _format_style_payload_text(self, style_payload: dict[str, Any]) -> str:
        segments = [
            f"绘画风格：{style_payload.get('painting_style', '手绘插画')}",
            f"色彩情绪：{style_payload.get('color_mood', '温暖治愈')}",
            f"提示词样例：{style_payload.get('prompt_example', '请保持统一风格')}",
        ]
        keywords = style_payload.get("extra_keywords") or []
        if isinstance(keywords, list) and keywords:
            segments.append("关键词：" + "、".join(str(item) for item in keywords if str(item).strip()))
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
                "draft_style_id": None,
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

    def _extract_image_count(self, selected_items: list[str], user_text: str) -> int | None:
        if selected_items:
            candidate = selected_items[0].strip().replace("张", "")
            if candidate.isdigit():
                image_count = int(candidate)
                if 1 <= image_count <= 10:
                    return image_count
        match = re.search(r"\b([1-9]|10)\b", user_text)
        if match:
            return int(match.group(1))
        return None

    def _ensure_image_count_options(self, options: dict[str, Any] | None) -> dict[str, Any]:
        if isinstance(options, dict):
            title = options.get("title")
            items = options.get("items")
            max_value = options.get("max")
            if isinstance(title, str) and isinstance(items, list) and items and isinstance(max_value, int) and max_value == 1:
                return options
        return {"title": "请选择生成数量", "items": ["1", "2", "3", "4"], "max": 1}

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
        foods: list[str] = []
        scenes: list[str] = []
        keywords: list[str] = []
        source_asset_ids: list[str] = []
        for asset in assets:
            asset_id = asset.get("id")
            if isinstance(asset_id, str):
                source_asset_ids.append(asset_id)
            content = (asset.get("content") or "").strip()
            asset_type = asset.get("asset_type")
            if asset_type == "food_name" and content:
                self._append_unique(foods, [content])
                self._append_unique(keywords, [content])
                continue
            if asset_type == "scenic_name" and content:
                self._append_unique(scenes, [content])
                self._append_unique(keywords, [content])
                continue
            for token in self._tokenize_text(content):
                self._append_unique(keywords, [token])
                if any(marker in token for marker in FOOD_MARKERS):
                    self._append_unique(foods, [token])
                if any(marker in token for marker in SCENE_MARKERS):
                    self._append_unique(scenes, [token])
        for hint_token in self._tokenize_text(user_hint):
            self._append_unique(keywords, [hint_token])
        if not foods:
            self._append_unique(foods, keywords[:3])
        if not scenes:
            self._append_unique(scenes, keywords[:3])
        confidence = min(1.0, (len(foods) + len(scenes)) / 8.0)
        return {
            "foods": foods[:10],
            "scenes": scenes[:10],
            "keywords": keywords[:20],
            "source_asset_ids": source_asset_ids,
            "confidence": round(confidence, 2),
        }

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

    def _tokenize_text(self, text: str) -> list[str]:
        if not text:
            return []
        parts = re.split(r"[，,。；;、\s]+", text)
        tokens: list[str] = []
        for part in parts:
            token = part.strip()
            if len(token) >= 2:
                tokens.append(token)
        return tokens

    def _append_unique(self, target: list[str], values: list[str]) -> None:
        seen = set(target)
        for value in values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            target.append(text)

    def _build_style_context(self, state: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
        style_profile_id = state.get("draft_style_id")
        style_name = profile.get("name") if profile else None
        style_payload = self._build_style_payload(state)
        sample_image_asset_id = style_payload.get("sample_image_asset_id")
        sample_preview_url = self.style_service._resolve_sample_image_preview_url(sample_image_asset_id)
        return {
            "style_profile_id": style_profile_id,
            "style_name": style_name,
            "sample_image_asset_id": sample_image_asset_id,
            "sample_image_preview_url": sample_preview_url,
            "style_payload": style_payload,
        }

    def _build_response(self, session_id: str, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "messages": self.inspiration_repo.list_messages(session_id),
            "draft": {
                "stage": state.get("stage", "style_collecting"),
                "style_payload": self._build_style_payload(state),
                "image_count": state.get("image_count"),
                "draft_style_id": state.get("draft_style_id"),
                "locked": bool(state.get("locked")),
            },
        }
