from __future__ import annotations

import json
import logging
from typing import Any

from backend.app.core.errors import DomainError
from backend.app.core.utils import new_id, now_iso
from backend.app.infra.http_client import (
    HttpClientHttpError,
    HttpClientInvalidJsonError,
    HttpClientInvalidPayloadError,
    HttpClientNetworkError,
    post_json,
)
from backend.app.workers.generation.text_model_retry import build_text_model_name_candidates, should_retry_text_model_name

logger = logging.getLogger(__name__)


class CopyModelError(Exception):
    def __init__(self, reason: str, detail: str | None = None):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


class GenerationCopyGenerationMixin:
    def _generate_copy_result(
        self,
        *,
        job: dict[str, Any],
        style: dict[str, Any],
        images: list[dict[str, Any]],
        content_mode: str,
        breakdown: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            provider, model_name = self._resolve_text_model_provider()
            system_prompt = self._build_copy_system_prompt(content_mode)
            user_prompt = self._build_copy_user_prompt(
                style=style,
                images=images,
                content_mode=content_mode,
                breakdown=breakdown,
            )
            raw_text = self._call_text_model_for_copy(
                provider=provider,
                model_name=model_name,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            try:
                payload = self._parse_copy_payload(raw_text)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                retry_text = self._call_text_model_for_copy(
                    provider=provider,
                    model_name=model_name,
                    system_prompt=self._build_copy_system_prompt(content_mode, strict_json=True),
                    user_prompt=(
                        f"{user_prompt}\n"
                        "上一次输出未满足 JSON 结构，请仅输出一个合法 JSON 对象，不要 Markdown、不要解释。"
                    ),
                )
                payload = self._parse_copy_payload(retry_text)
            return self._normalize_copy_payload(job=job, payload=payload)
        except CopyModelError as error:
            raise DomainError(code="E-1004", message=f"文案生成失败：{error.detail}", status_code=503) from error
        except DomainError:
            raise
        except (ValueError, KeyError, TypeError) as error:
            raise DomainError(code="E-1004", message="文案生成失败：模型返回格式异常，请重试", status_code=503) from error

    def _resolve_text_model_provider(self) -> tuple[dict[str, Any], str]:
        routing = self.model_service.require_routing()
        text_model = routing.get("text_model") or {}
        provider_id = text_model.get("provider_id")
        model_name = text_model.get("model_name")
        if not provider_id or not model_name:
            raise DomainError(code="E-1006", message="请先完成模型设置", status_code=400)
        provider = self.model_service.provider_repo.get(provider_id)
        if not provider or not provider.get("enabled"):
            raise DomainError(code="E-1006", message="文字模型提供商不可用", status_code=400)
        return provider, model_name

    def _build_copy_system_prompt(self, content_mode: str, strict_json: bool = False) -> str:
        mode_prompt = {
            "food": "突出食材细节、操作步骤和可复刻性。",
            "scenic": "突出场景氛围、动线和镜头叙事。",
            "food_scenic": "兼顾食材表达与场景叙事，强调融合感。",
        }.get(content_mode, "输出高质量可发布图文内容。")
        return (
            "你是 Savory Canvas 资深内容总编。"
            "请输出严格 JSON，不要输出 Markdown。"
            "JSON 结构必须为："
            '{"title":"", "intro":"", "guide_sections":[{"heading":"","content":""}], "ending":"", "full_text":""}。'
            f"{mode_prompt}"
            "要求：中文表达自然、专业、可发布，避免口号化空话。"
            "标题要具体有吸引力，导语要给出清晰价值承诺。"
            "guide_sections 至少 3 段，每段需可执行，建议包含“看点/路线/实操建议/避坑提示”等。"
            "ending 需包含行动引导（收藏、评论、到店/到景点打卡等）。"
            "full_text 必须是可直接发布的完整长文，不是字段拼接。"
            + ("本次必须只输出一个 JSON 对象，不允许任何额外文本。" if strict_json else "")
        )

    def _build_copy_user_prompt(
        self,
        *,
        style: dict[str, Any],
        images: list[dict[str, Any]],
        content_mode: str,
        breakdown: dict[str, Any],
    ) -> str:
        extracted = breakdown.get("extracted") or {}
        foods = "、".join(extracted.get("foods") or []) or "无"
        scenes = "、".join(extracted.get("scenes") or []) or "无"
        keywords = "、".join(extracted.get("keywords") or []) or "无"
        image_count = len(images)
        sections = [str(item.get("heading") or "").strip() for item in (style.get("style_payload") or {}).get("copy_sections") or []]
        section_text = "、".join(item for item in sections if item) or "看点、路线、实操建议、避坑提示"
        return (
            f"风格名称：{style.get('name', '未命名风格')}\n"
            f"风格配置：{self._format_style_payload(style.get('style_payload') or {})}\n"
            f"内容模式：{content_mode}\n"
            f"提取食材：{foods}\n"
            f"提取场景：{scenes}\n"
            f"关键词：{keywords}\n"
            f"配图数量：{image_count}\n"
            f"建议章节方向：{section_text}\n"
            "文案目标：围绕地点/景点/美食这些资产写可发布攻略，不要写成“图片说明”。\n"
            "禁止出现“第一张图/第二张图/本图/画面里”等描述图像的措辞。\n"
            "请直接输出可发布图文文案。"
        )

    def _call_text_model_for_copy(
        self,
        *,
        provider: dict[str, Any],
        model_name: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        provider_id = str(provider.get("id") or "").strip()
        max_attempts = 3
        last_error: CopyModelError | None = None
        model_candidates = build_text_model_name_candidates(model_name)
        for candidate_index, candidate_name in enumerate(model_candidates):
            last_error = None
            for attempt in range(1, max_attempts + 1):
                protocol_order = self._build_text_protocol_order(provider_id, provider.get("api_protocol"))
                for index, protocol in enumerate(protocol_order):
                    endpoint, payload = self._build_text_protocol_payload(
                        provider=provider,
                        model_name=candidate_name,
                        protocol=protocol,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                    )
                    try:
                        response_payload = self._post_text_json(
                            endpoint=endpoint,
                            api_key=provider["api_key"],
                            payload=payload,
                            provider_id=provider_id,
                            model_name=candidate_name,
                        )
                        content = self._extract_text_from_text_payload(response_payload, protocol)
                        if not content.strip():
                            raise CopyModelError("empty_content", "上游未返回文案内容")
                        if provider_id:
                            self._text_protocol_overrides[provider_id] = protocol
                        return content
                    except CopyModelError as error:
                        last_error = error
                        if index == 0 and self._should_retry_text_protocol(error):
                            logger.warning(
                                "文案协议回退重试: from=%s to=%s provider=%s model=%s reason=%s detail=%s",
                                protocol,
                                protocol_order[1],
                                provider_id or "-",
                                candidate_name,
                                error.reason,
                                error.detail,
                            )
                            continue
                        break
                if last_error and attempt < max_attempts and self._should_retry_text_request(last_error):
                    logger.warning(
                        "文案模型重试: attempt=%s/%s provider=%s model=%s reason=%s detail=%s",
                        attempt,
                        max_attempts,
                        provider_id or "-",
                        candidate_name,
                        last_error.reason,
                        last_error.detail,
                    )
                    continue
                break
            if candidate_index < len(model_candidates) - 1 and last_error and should_retry_text_model_name(last_error.reason, last_error.detail):
                logger.warning(
                    "文案模型名降级重试: from=%s to=%s provider=%s reason=%s detail=%s",
                    candidate_name,
                    model_candidates[candidate_index + 1],
                    provider_id or "-",
                    last_error.reason,
                    last_error.detail,
                )
                continue
        raise CopyModelError("protocol_both_failed", last_error.detail if last_error else "双协议请求失败")

    def _build_text_protocol_order(self, provider_id: str, configured_protocol: str | None) -> list[str]:
        override_protocol = self._text_protocol_overrides.get(provider_id)
        active_protocol = override_protocol or configured_protocol
        if active_protocol == "chat_completions":
            return ["chat_completions", "responses"]
        return ["responses", "chat_completions"]

    def _build_text_protocol_payload(
        self,
        *,
        provider: dict[str, Any],
        model_name: str,
        protocol: str,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, dict[str, Any]]:
        base_url = provider["base_url"].rstrip("/")
        if protocol == "responses":
            return (
                f"{base_url}/responses",
                {
                    "model": model_name,
                    "instructions": system_prompt,
                    "input": [{"role": "user", "content": [{"type": "input_text", "text": user_prompt}]}],
                    "temperature": 0.2,
                },
            )
        return (
            f"{base_url}/chat/completions",
            {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
            },
        )

    def _post_text_json(
        self,
        *,
        endpoint: str,
        api_key: str,
        payload: dict[str, Any],
        provider_id: str,
        model_name: str,
    ) -> dict[str, Any]:
        try:
            return post_json(endpoint, payload, api_key, timeout=45)
        except HttpClientHttpError as error:
            if error.status_code in {404, 405}:
                raise CopyModelError("protocol_endpoint_not_supported", f"HTTP {error.status_code}") from error
            if self._looks_like_text_protocol_incompatible(error.status_code, error.body_text):
                raise CopyModelError("protocol_incompatible", f"HTTP {error.status_code}") from error
            reason = "upstream_retryable_http" if error.status_code in {408, 409, 425, 429, 500, 502, 503, 504} else "upstream_http_error"
            logger.warning(
                "文案上游 HTTP 失败: provider_id=%s model_name=%s endpoint=%s status=%s",
                provider_id or "-",
                model_name,
                endpoint,
                error.status_code,
            )
            detail = self._build_upstream_http_error_detail(status_code=error.status_code, body_text=error.body_text)
            raise CopyModelError(reason, detail) from error
        except HttpClientNetworkError as error:
            logger.warning(
                "文案上游网络失败: provider_id=%s model_name=%s endpoint=%s reason=%s",
                provider_id or "-",
                model_name,
                endpoint,
                error.detail,
            )
            normalized_error = self._normalize_upstream_error_text(error.detail)
            detail = normalized_error or "network"
            raise CopyModelError("upstream_network", f"上游网络异常（上游：{detail}）") from error
        except HttpClientInvalidJsonError as error:
            raise CopyModelError("upstream_invalid_json", "上游文案响应不是有效 JSON") from error
        except HttpClientInvalidPayloadError as error:
            raise CopyModelError("upstream_invalid_payload", "上游文案响应结构非法") from error

    def _looks_like_text_protocol_incompatible(self, status_code: int, body_text: str) -> bool:
        lowered = body_text.lower()
        common_markers = ["unsupported", "not support", "invalid_request_error", "messages", "input", "instructions"]
        if status_code == 400:
            return any(marker in lowered for marker in common_markers)
        if status_code == 403 and self._looks_like_html_error_page(body_text):
            return True
        if status_code >= 500:
            server_markers = ["not implemented", "convert_request_failed", "new_api_error"]
            return any(marker in lowered for marker in (common_markers + server_markers))
        return False

    def _should_retry_text_protocol(self, error: CopyModelError) -> bool:
        return error.reason in {"protocol_endpoint_not_supported", "protocol_incompatible"}

    def _should_retry_text_request(self, error: CopyModelError) -> bool:
        return error.reason in {"upstream_network", "upstream_retryable_http"}

    def _extract_text_from_text_payload(self, payload: dict[str, Any], protocol: str) -> str:
        if protocol == "responses":
            output_text = payload.get("output_text")
            if isinstance(output_text, str) and output_text.strip():
                return output_text
            blocks = payload.get("output")
            if isinstance(blocks, list):
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    content_list = block.get("content")
                    if not isinstance(content_list, list):
                        continue
                    for part in content_list:
                        if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"].strip():
                            return part["text"]
            raise CopyModelError("responses_missing_text", "responses 未返回文本")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise CopyModelError("chat_missing_choices", "chat_completions 未返回 choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip():
                    return item["text"]
        raise CopyModelError("chat_missing_text", "chat_completions 未返回文本")

    def _parse_copy_payload(self, model_text: str) -> dict[str, Any]:
        text = model_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("文案输出缺少 JSON 对象")
        payload = json.loads(text[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("文案输出 JSON 结构非法")
        return payload

    def _normalize_copy_payload(self, *, job: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title", "")).strip()
        intro = str(payload.get("intro", "")).strip()
        ending = str(payload.get("ending", "")).strip()
        sections = payload.get("guide_sections")
        full_text = str(payload.get("full_text", "")).strip()
        if not title or not intro or not ending:
            raise ValueError("文案基础字段为空")
        if not isinstance(sections, list) or len(sections) < 2:
            raise ValueError("文案段落数量不足")
        normalized_sections: list[dict[str, str]] = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            heading = str(section.get("heading", "")).strip()
            content = str(section.get("content", "")).strip()
            if heading and content:
                normalized_sections.append({"heading": heading, "content": content})
        if len(normalized_sections) < 2:
            raise ValueError("文案段落结构非法")
        if not full_text:
            full_text = "\n".join(
                [title, intro] + [f"{item['heading']}：{item['content']}" for item in normalized_sections] + [ending]
            )
        return {
            "id": new_id(),
            "job_id": job["id"],
            "title": title,
            "intro": intro,
            "guide_sections": normalized_sections,
            "ending": ending,
            "full_text": full_text,
            "created_at": now_iso(),
        }
