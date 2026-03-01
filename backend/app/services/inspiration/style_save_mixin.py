from __future__ import annotations

import json
from typing import Any

from backend.app.core.errors import DomainError
from backend.app.core.utils import new_id, now_iso
from backend.app.services.inspiration.constants import STYLE_SAVE_SUMMARY_SYSTEM_PROMPT


class InspirationStyleSaveMixin:
    def _create_saved_style(self, session: dict[str, Any], state: dict[str, Any]) -> None:
        now = now_iso()
        style_name, style_payload = self._summarize_style_for_save(session_id=session["id"], state=state)
        self.style_repo.create(
            {
                "id": new_id(),
                "session_id": session["id"],
                "name": style_name,
                "style_payload": style_payload,
                "is_builtin": False,
                "created_at": now,
                "updated_at": now,
            }
        )

    def _summarize_style_for_save(
        self,
        *,
        session_id: str,
        state: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        base_payload = self._build_style_payload(state)
        recent_context = self._collect_recent_user_context(session_id, limit=10) or "无"
        style_text = self._format_style_payload_text(base_payload)
        draft_prompt = str(state.get("style_prompt") or base_payload.get("style_prompt") or "").strip()
        image_refs = self._collect_recent_image_assets_for_style_save(session_id=session_id, limit=8)
        user_prompt = self._build_style_save_user_prompt(
            style_text=style_text,
            draft_prompt=draft_prompt,
            recent_context=recent_context,
            image_refs=image_refs,
        )
        image_urls = [ref["preview_url"] for ref in image_refs]
        if image_urls:
            model_text = self._call_vision_model_with_retry(
                session_id=session_id,
                system_prompt=STYLE_SAVE_SUMMARY_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                image_urls=image_urls,
                strict_json=True,
            )
        else:
            model_text = self._call_text_model_with_retry(
                session_id=session_id,
                system_prompt=STYLE_SAVE_SUMMARY_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                strict_json=True,
            )
        payload = self._parse_style_save_summary_payload(model_text)
        style_name = str(payload.get("style_name") or "").strip()
        if not style_name:
            style_name = f"灵感风格-{now_iso()[11:19].replace(':', '')}"
        painting_style = str(payload.get("painting_style") or base_payload.get("painting_style") or "手绘插画").strip()
        color_mood = str(payload.get("color_mood") or base_payload.get("color_mood") or "温暖治愈").strip()
        prompt_example = str(
            payload.get("prompt_example")
            or base_payload.get("prompt_example")
            or base_payload.get("style_prompt")
            or "请保持统一风格与清晰图文布局。"
        ).strip()
        if not prompt_example:
            prompt_example = "请保持统一风格与清晰图文布局。"
        keywords = self._normalize_style_keywords(payload.get("extra_keywords"))
        selected_indexes = self._normalize_style_image_indexes(payload.get("style_image_indexes"), max_index=len(image_refs))
        sample_image_asset_ids: list[str] = []
        seen: set[str] = set()
        for index in selected_indexes:
            asset_id = image_refs[index - 1]["asset_id"]
            if asset_id in seen:
                continue
            seen.add(asset_id)
            sample_image_asset_ids.append(asset_id)
        style_seed = {
            "painting_style": painting_style,
            "color_mood": color_mood,
            "prompt_example": prompt_example,
            "style_prompt": prompt_example,
            "extra_keywords": keywords,
            "sample_image_asset_ids": sample_image_asset_ids,
        }
        normalized_payload = self.style_service._normalize_style_payload(style_seed)
        self.style_service._validate_sample_image_assets(
            session_id=session_id,
            style_payload=normalized_payload,
            strict=False,
        )
        self.style_service._sync_sample_image_snapshot(normalized_payload, previous_payload=None)
        return style_name, normalized_payload

    def _collect_recent_image_assets_for_style_save(
        self,
        *,
        session_id: str,
        limit: int,
    ) -> list[dict[str, str]]:
        assets = self.asset_repo.list_by_session(session_id)
        refs: list[dict[str, str]] = []
        for asset in reversed(assets):
            if asset.get("asset_type") != "image":
                continue
            asset_id = str(asset.get("id") or "").strip()
            if not asset_id:
                continue
            preview_url = self.style_service._build_public_image_url(asset.get("file_path"))
            if not preview_url:
                continue
            refs.append(
                {
                    "asset_id": asset_id,
                    "name": str(asset.get("content") or "").strip() or "图片素材",
                    "preview_url": preview_url,
                }
            )
            if len(refs) >= limit:
                break
        refs.reverse()
        return refs

    def _build_style_save_user_prompt(
        self,
        *,
        style_text: str,
        draft_prompt: str,
        recent_context: str,
        image_refs: list[dict[str, str]],
    ) -> str:
        lines = [
            f"当前草案风格参数：{style_text}",
            f"当前草案母提示词：{draft_prompt or '无'}",
            f"最近用户上下文：{recent_context}",
        ]
        if image_refs:
            lines.append("以下为可判定图片（序号从 1 开始，对应输入图片顺序）：")
            for index, ref in enumerate(image_refs, start=1):
                lines.append(f"{index}. asset_id={ref['asset_id']}，文件名={ref['name']}")
        else:
            lines.append("当前无可用图片，请仅根据文本总结风格。")
        lines.append("请输出严格 JSON。")
        return "\n".join(lines)

    def _parse_style_save_summary_payload(self, model_text: str) -> dict[str, Any]:
        text = model_text.strip()
        if text.startswith("```"):
            lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()
        start_index = text.find("{")
        end_index = text.rfind("}")
        if start_index == -1 or end_index == -1 or end_index < start_index:
            raise DomainError(code="E-1004", message="保存风格失败：模型返回格式异常，请重试", status_code=503)
        json_text = text[start_index : end_index + 1]
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as error:
            raise DomainError(code="E-1004", message="保存风格失败：模型返回格式异常，请重试", status_code=503) from error
        if not isinstance(payload, dict):
            raise DomainError(code="E-1004", message="保存风格失败：模型返回格式异常，请重试", status_code=503)
        return payload

    def _normalize_style_keywords(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return self._normalize_asset_list(value)[:20]
        if isinstance(value, str):
            parts = [item.strip() for item in value.replace("，", ",").split(",")]
            return self._normalize_asset_list(parts)[:20]
        return []

    def _normalize_style_image_indexes(self, value: Any, *, max_index: int) -> list[int]:
        if not isinstance(value, list) or max_index <= 0:
            return []
        normalized: list[int] = []
        seen: set[int] = set()
        for item in value:
            if isinstance(item, int):
                index = item
            elif isinstance(item, str) and item.strip().isdigit():
                index = int(item.strip())
            else:
                continue
            if index < 1 or index > max_index or index in seen:
                continue
            seen.add(index)
            normalized.append(index)
        return normalized
