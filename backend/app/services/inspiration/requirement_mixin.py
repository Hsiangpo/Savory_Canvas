from __future__ import annotations

from typing import Any

from backend.app.core.errors import DomainError
from backend.app.services.inspiration.constants import STYLE_REQUIREMENT_SYSTEM_PROMPT


class InspirationRequirementMixin:
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
        session_image_urls = self._collect_latest_user_image_urls(session["id"])
        if session_image_urls:
            model_reply = self._call_vision_model_with_retry(
                session_id=session["id"],
                system_prompt=STYLE_REQUIREMENT_SYSTEM_PROMPT,
                user_prompt=f"{user_prompt}\n用户已上传图片，请先读取图片再引导。",
                image_urls=session_image_urls,
                strict_json=False,
            ).strip()
        else:
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
        session_image_urls = self._collect_latest_user_image_urls(session["id"])
        if session_image_urls:
            model_reply = self._call_vision_model_with_retry(
                session_id=session["id"],
                system_prompt=STYLE_REQUIREMENT_SYSTEM_PROMPT,
                user_prompt=f"{user_prompt}\n用户已上传图片，请先读取图片再引导。",
                image_urls=session_image_urls,
                strict_json=False,
            ).strip()
        else:
            model_reply = self._call_text_model_with_retry(
                session_id=session["id"],
                system_prompt=STYLE_REQUIREMENT_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                strict_json=False,
            ).strip()
        if model_reply:
            return model_reply
        raise DomainError(code="E-1004", message="模型输出为空，请稍后重试", status_code=503)

    def _collect_latest_user_image_urls(self, session_id: str) -> list[str]:
        messages = self.inspiration_repo.list_messages(session_id)
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            attachments = message.get("attachments")
            if not isinstance(attachments, list):
                continue
            urls: list[str] = []
            seen: set[str] = set()
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                if attachment.get("type") != "image":
                    continue
                if self._is_style_reference_attachment(attachment):
                    continue
                preview_url = attachment.get("preview_url")
                if isinstance(preview_url, str) and preview_url.strip():
                    normalized_url = self.style_service._build_public_image_url(preview_url) or preview_url.strip()
                    if normalized_url not in seen:
                        seen.add(normalized_url)
                        urls.append(normalized_url)
                        continue
                asset_id = attachment.get("asset_id") or attachment.get("id")
                if not isinstance(asset_id, str) or not asset_id.strip():
                    continue
                asset = self.asset_repo.get(asset_id.strip())
                if not asset or asset.get("asset_type") != "image":
                    continue
                public_url = self.style_service._build_public_image_url(asset.get("file_path"))
                if not public_url or public_url in seen:
                    continue
                seen.add(public_url)
                urls.append(public_url)
            if urls:
                return urls[:4]
        return []

    def _is_style_reference_attachment(self, attachment: dict[str, Any]) -> bool:
        usage_type = attachment.get("usage_type")
        if isinstance(usage_type, str) and usage_type.strip() == "style_reference":
            return True
        name = attachment.get("name")
        if isinstance(name, str) and name.strip().startswith("风格样例图"):
            return True
        return False
