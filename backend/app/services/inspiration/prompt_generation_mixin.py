from __future__ import annotations

import logging
import re
from typing import Any

from backend.app.core.errors import DomainError
from backend.app.services.inspiration.constants import (
    STYLE_PROMPT_RETRY_SYSTEM_PROMPT,
    STYLE_PROMPT_SYSTEM_PROMPT,
)
from backend.app.services.style_service import StyleFallbackError

logger = logging.getLogger(__name__)


class InspirationPromptGenerationMixin:
    def _generate_style_prompt(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        feedback: str,
    ) -> str:
        style_payload = self._build_style_payload(state)
        image_count = state.get("image_count") or 1
        style_text = self._format_style_payload_text(style_payload)
        requirement_hint = self._build_prompt_requirement_hint(session["id"], feedback)
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

    def _build_prompt_requirement_hint(self, session_id: str, feedback: str) -> str:
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
        return any(marker in text for marker in markers)

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
