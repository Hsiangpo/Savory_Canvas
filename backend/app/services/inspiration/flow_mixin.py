from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from backend.app.core.errors import DomainError
from backend.app.core.utils import new_id, now_iso
from backend.app.services.inspiration.constants import (
    ALLOCATION_PLAN_SYSTEM_PROMPT,
    ASSET_CONFIRM_HINT,
    ASSET_EXTRACT_SYSTEM_PROMPT,
    IMAGE_ASSET_EXTRACT_SYSTEM_PROMPT,
    IMAGE_COUNT_EXTRACT_SYSTEM_PROMPT,
    PROMPT_ACTION_OPTIONS,
    PROMPT_READINESS_SYSTEM_PROMPT,
    VISION_ERROR_MESSAGE,
    WELCOME_MESSAGE,
)
from backend.app.services.inspiration.requirement_mixin import InspirationRequirementMixin
from backend.app.services.inspiration.style_save_mixin import InspirationStyleSaveMixin
from backend.app.services.style_service import StyleFallbackError

logger = logging.getLogger(__name__)


class InspirationFlowMixin(InspirationRequirementMixin, InspirationStyleSaveMixin):
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
        locations = "、".join(candidates.get("locations") or []) or "无"
        foods = "、".join(candidates.get("foods") or []) or "无"
        scenes = "、".join(candidates.get("scenes") or []) or "无"
        keywords = "、".join(candidates.get("keywords") or []) or "无"
        style_text = self._format_style_payload_text(self._build_style_payload(state))
        recent_context = self._collect_recent_user_context(session["id"], limit=8) or "无"
        user_prompt = (
            f"目标张数：{image_count}\n"
            f"风格参数：{style_text}\n"
            f"已提取地点：{locations}\n"
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
        candidate_locations = self._normalize_asset_list(
            asset_candidates.get("locations") if isinstance(asset_candidates, dict) else [],
        )
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
                raise DomainError(code="E-1004", message="分图确认失败：缺少可追溯素材来源，请重试", status_code=503)
            locations = self._normalize_asset_list(item.get("locations"))
            if not locations and candidate_locations:
                locations = candidate_locations[:4]
            scenes = self._normalize_asset_list(item.get("scenes"))
            foods = self._normalize_asset_list(item.get("foods"))
            keywords = self._normalize_asset_list(item.get("keywords"))
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
    def _extract_asset_candidates(self, session_id: str, user_hint: str, style_prompt: str = "") -> dict[str, Any]:
        assets = self.asset_repo.list_by_session(session_id)
        source_assets = [asset for asset in assets if isinstance(asset.get("id"), str)]
        source_asset_ids = [str(asset["id"]) for asset in source_assets if isinstance(asset.get("id"), str)]
        text_context = self._build_asset_text_context(session_id, source_assets, user_hint, style_prompt=style_prompt)
        text_extracted = self._extract_assets_with_llm(session_id, text_context)
        image_urls = self._collect_image_urls_from_assets(source_assets)
        image_extracted: dict[str, Any] | None = None
        if image_urls:
            image_extracted = self._extract_image_assets_with_llm(session_id, image_urls, text_context)
        merged = self._merge_text_and_image_assets(text_extracted, image_extracted)
        locations = merged["locations"]
        scenes = merged["scenes"]
        foods = merged["foods"]
        keywords = self._merge_keyword_values(merged["keywords"], locations + scenes + foods)
        confidence = merged["confidence"]
        return {
            "locations": locations[:8],
            "foods": foods[:10],
            "scenes": scenes[:10],
            "keywords": keywords[:20],
            "source_asset_ids": source_asset_ids,
            "confidence": confidence,
        }

    def _build_asset_text_context(
        self,
        session_id: str,
        source_assets: list[dict[str, Any]],
        user_hint: str,
        style_prompt: str = "",
    ) -> str:
        parts: list[str] = []
        if user_hint.strip():
            parts.append(f"用户本轮补充：{user_hint.strip()}")
        normalized_style_prompt = style_prompt.strip()
        if normalized_style_prompt:
            if len(normalized_style_prompt) > 1800:
                normalized_style_prompt = f"{normalized_style_prompt[:1800]}..."
            parts.append(f"当前母提示词：{normalized_style_prompt}")
        recent_user_context = self._collect_recent_user_context(session_id, limit=8)
        if recent_user_context:
            parts.append(f"近期用户上下文：{recent_user_context}")
        recent_text_assets: list[tuple[str, str]] = []
        for asset in reversed(source_assets):
            asset_type = str(asset.get("asset_type") or "")
            content = str(asset.get("content") or "").strip()
            if asset_type not in {"food_name", "scenic_name", "text", "transcript"} or not content:
                continue
            recent_text_assets.append((asset_type, content))
            if len(recent_text_assets) >= 8:
                break
        for asset_type, content in reversed(recent_text_assets):
            parts.append(f"{asset_type}: {content}")
        return "\n".join(parts)

    def _collect_session_image_asset_urls(self, session_id: str) -> list[str]:
        assets = self.asset_repo.list_by_session(session_id)
        return self._collect_image_urls_from_assets(assets)

    def _collect_image_urls_from_assets(self, assets: list[dict[str, Any]]) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for asset in reversed(assets):
            if asset.get("asset_type") != "image":
                continue
            file_path = asset.get("file_path")
            public_url = self.style_service._build_public_image_url(file_path)
            if not public_url or public_url in seen:
                continue
            seen.add(public_url)
            urls.append(public_url)
            if len(urls) >= 4:
                break
        urls.reverse()
        return urls

    def _extract_image_assets_with_llm(
        self,
        session_id: str,
        image_urls: list[str],
        context_text: str,
    ) -> dict[str, Any]:
        self._ensure_vision_capable()
        normalized_context = context_text.strip() or "无"
        response_text = self._call_vision_model_with_retry(
            session_id=session_id,
            system_prompt=IMAGE_ASSET_EXTRACT_SYSTEM_PROMPT,
            user_prompt=(
                "请严格结合下列用户文本上下文，判断图片是风格参考还是内容素材后再提取资产。\n"
                f"用户文本上下文：\n{normalized_context}\n"
                "若属于风格参考图，请返回空数组。"
            ),
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

    def _merge_text_and_image_assets(
        self,
        text_extracted: dict[str, Any],
        image_extracted: dict[str, Any] | None,
    ) -> dict[str, Any]:
        text_locations = self._normalize_asset_list(text_extracted.get("locations"))
        text_scenes = self._normalize_asset_list(text_extracted.get("scenes"))
        text_foods = self._normalize_asset_list(text_extracted.get("foods"))
        text_keywords = self._normalize_asset_list(text_extracted.get("keywords"))
        text_confidence_raw = text_extracted.get("confidence")
        text_confidence = float(text_confidence_raw) if isinstance(text_confidence_raw, (int, float)) else 0.8
        text_confidence = min(1.0, max(0.0, text_confidence))
        if not image_extracted:
            return {
                "locations": text_locations,
                "scenes": text_scenes,
                "foods": text_foods,
                "keywords": text_keywords,
                "confidence": round(text_confidence, 2),
            }
        image_locations = self._normalize_asset_list(image_extracted.get("locations"))
        image_scenes = self._normalize_asset_list(image_extracted.get("scenes"))
        image_foods = self._normalize_asset_list(image_extracted.get("foods"))
        image_keywords = self._normalize_asset_list(image_extracted.get("keywords"))
        image_confidence_raw = image_extracted.get("confidence")
        image_confidence = float(image_confidence_raw) if isinstance(image_confidence_raw, (int, float)) else 0.8
        image_confidence = min(1.0, max(0.0, image_confidence))
        has_text_assets = bool(text_locations or text_scenes or text_foods)
        locations = text_locations or image_locations
        scenes = text_scenes or image_scenes
        foods = text_foods or image_foods
        if has_text_assets:
            keywords = self._merge_keyword_values(text_keywords, locations + scenes + foods)
            confidence = text_confidence
        else:
            keywords = self._merge_keyword_values(text_keywords, image_keywords + locations + scenes + foods)
            confidence = max(text_confidence, image_confidence)
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
        max_attempts = 3
        for attempt in range(max_attempts):
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
        locations = self._merge_keyword_values(base.get("locations"), incoming.get("locations") or [])
        foods = self._merge_keyword_values(base.get("foods"), incoming.get("foods") or [])
        scenes = self._merge_keyword_values(base.get("scenes"), incoming.get("scenes") or [])
        keywords = self._merge_keyword_values(base.get("keywords"), incoming.get("keywords") or [])
        source_asset_ids = self._merge_keyword_values(base.get("source_asset_ids"), incoming.get("source_asset_ids") or [])
        confidence = incoming.get("confidence") if isinstance(incoming.get("confidence"), (int, float)) else base.get("confidence")
        return {
            "locations": locations[:8],
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

