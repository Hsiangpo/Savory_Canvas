from __future__ import annotations

from typing import Any

from backend.app.core.errors import DomainError
from backend.app.core.utils import new_id, now_iso
from backend.app.services.inspiration.allocation_builder_mixin import InspirationAllocationBuilderMixin
from backend.app.services.inspiration.asset_extraction_mixin import InspirationAssetExtractionMixin
from backend.app.services.inspiration.content_recommendation_mixin import InspirationContentRecommendationMixin
from backend.app.services.inspiration.constants import (
    VISION_ERROR_MESSAGE,
    WELCOME_MESSAGE,
)
from backend.app.services.inspiration.style_save_mixin import InspirationStyleSaveMixin


class InspirationFlowMixin(
    InspirationContentRecommendationMixin,
    InspirationAssetExtractionMixin,
    InspirationAllocationBuilderMixin,
    InspirationStyleSaveMixin,
):
    def _get_latest_assistant_turn_context(self, session_id: str) -> dict[str, Any]:
        messages = self.inspiration_repo.list_messages(session_id)
        for message in reversed(messages):
            if message.get("role") != "assistant":
                continue
            reply = str(message.get("content") or "").strip().replace("\n", " ")
            if len(reply) > 180:
                reply = f"{reply[:180]}..."
            options = message.get("options") if isinstance(message.get("options"), dict) else None
            return {"reply": reply, "options": options, "stage": str(message.get("stage") or "").strip() or None}
        return {"reply": "", "options": None, "stage": None}

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
                "stage": "initial_understanding",
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
                "progress": 10,
                "progress_label": "初始了解",
                "active_job_id": None,
                "updated_at": now_iso(),
            }
        )

    def _ensure_draft_style_profile(self, session: dict[str, Any], state: dict[str, Any]) -> str:
        draft_style_id = str(state.get("draft_style_id") or "").strip()
        style_payload = self._build_style_payload(state)
        now = now_iso()
        if draft_style_id and self.style_repo.get(draft_style_id):
            self.style_repo.update_payload(draft_style_id, style_payload, now)
            return draft_style_id
        created = self.style_repo.create(
            {
                "id": new_id(),
                "session_id": session["id"],
                "name": "灵感草稿",
                "style_payload": style_payload,
                "is_builtin": False,
                "created_at": now,
                "updated_at": now,
            }
        )
        state["draft_style_id"] = created["id"]
        return created["id"]

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

    def _ingest_ready_transcripts(self, session_id: str, state: dict[str, Any]) -> list[dict[str, Any]]:
        seen_ids = set(state.get("transcript_seen_ids") or [])
        changed = False
        new_transcripts: list[dict[str, Any]] = []
        for asset in self.asset_repo.list_by_session(session_id):
            if asset.get("asset_type") != "transcript" or asset.get("status") != "ready":
                continue
            transcript_id = asset["id"]
            if transcript_id in seen_ids:
                continue
            changed = True
            seen_ids.add(transcript_id)
            new_transcripts.append(asset)
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
        return new_transcripts

    def _should_autorun_from_transcripts(self, transcripts: list[dict[str, Any]]) -> bool:
        for asset in transcripts:
            content = str(asset.get("content") or "").strip()
            if not content:
                continue
            if content.startswith("视频已上传，暂未获取到可用转写内容"):
                continue
            if content.startswith("已完成视频转写："):
                continue
            return True
        return False

    def _build_video_transcribing_turn(self) -> dict[str, Any]:
        return {
            "reply": "视频已收到，我先帮你转写里面的语音内容。转写完成后，我会自动继续整理里面提到的城市、景点和美食；如果你已经有明确方向，也可以现在直接补充给我。",
            "stage": "transcribing_video",
            "locked": False,
            "progress": 12,
            "progress_label": "视频转写中",
            "options": {"items": [
                {"label": "我先补充想做的城市和主题", "action_hint": "provide_city_theme_assets"},
                {"label": "等视频转写完成后继续", "action_hint": "wait_for_transcript"}
            ]},
            "trace": [],
        }

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
        sample_image_asset_ids = payload.get("sample_image_asset_ids")
        if not isinstance(sample_image_asset_ids, list):
            sample_image_asset_id = payload.get("sample_image_asset_id")
            sample_image_asset_ids = [sample_image_asset_id] if isinstance(sample_image_asset_id, str) and sample_image_asset_id.strip() else []
        sample_preview_urls = self.style_service._resolve_sample_image_preview_urls(payload)
        for index, sample_preview_url in enumerate(sample_preview_urls):
            if not isinstance(sample_preview_url, str) or not sample_preview_url.strip():
                continue
            asset_id = sample_image_asset_ids[index] if index < len(sample_image_asset_ids) else f"style-sample-{profile.get('id', '')}-{index + 1}"
            merged_attachments.append(
                self._build_attachment(
                    asset_id=asset_id,
                    attachment_type="image",
                    name=f"风格样例图{index + 1}",
                    preview_url=sample_preview_url,
                    status="ready",
                    usage_type="style_reference",
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

    def _build_style_context(self, state: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
        style_profile_id = state.get("draft_style_id")
        style_name = profile.get("name") if profile else None
        style_payload = self._build_style_payload(state)
        sample_image_asset_ids = style_payload.get("sample_image_asset_ids")
        if not isinstance(sample_image_asset_ids, list):
            legacy_asset_id = style_payload.get("sample_image_asset_id")
            sample_image_asset_ids = [legacy_asset_id] if isinstance(legacy_asset_id, str) and legacy_asset_id.strip() else []
        sample_preview_urls = self.style_service._resolve_sample_image_preview_urls(style_payload)
        sample_image_asset_id = sample_image_asset_ids[0] if sample_image_asset_ids else None
        sample_preview_url = sample_preview_urls[0] if sample_preview_urls else None
        return {
            "style_profile_id": style_profile_id,
            "style_name": style_name,
            "sample_image_asset_id": sample_image_asset_id,
            "sample_image_asset_ids": sample_image_asset_ids,
            "sample_image_preview_url": sample_preview_url,
            "sample_image_preview_urls": sample_preview_urls,
            "style_payload": style_payload,
        }

    def _build_response(self, session_id: str, state: dict[str, Any]) -> dict[str, Any]:
        messages = self.inspiration_repo.list_messages(session_id)
        self._hydrate_attachment_preview_urls(messages)
        agent_meta = None
        build_agent_meta = getattr(self, "_build_agent_meta", None)
        if callable(build_agent_meta):
            agent_meta = build_agent_meta(session_id, state)
        latest_options = None
        for message in reversed(messages):
            if message.get("role") not in {"assistant", "system"}:
                continue
            latest_options = message.get("options") if isinstance(message.get("options"), dict) else None
            break
        return {
            "session_id": session_id,
            "messages": messages,
            "draft": {
                "stage": state.get("stage", "initial_understanding"),
                "style_payload": self._build_style_payload(state),
                "image_count": state.get("image_count"),
                "draft_style_id": state.get("draft_style_id"),
                "allocation_plan": state.get("allocation_plan") if isinstance(state.get("allocation_plan"), list) else [],
                "options": latest_options,
                "progress": state.get("progress"),
                "progress_label": state.get("progress_label"),
                "active_job_id": state.get("active_job_id"),
                "locked": bool(state.get("locked")),
            },
            "agent": agent_meta,
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
