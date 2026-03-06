from __future__ import annotations

import json
import logging
from typing import Any

from backend.app.core.errors import DomainError
from backend.app.services.inspiration.constants import ASSET_EXTRACT_SYSTEM_PROMPT, IMAGE_ASSET_EXTRACT_SYSTEM_PROMPT
from backend.app.services.style.errors import StyleFallbackError

logger = logging.getLogger(__name__)


class InspirationAssetExtractionMixin:
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
