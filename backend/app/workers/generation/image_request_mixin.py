from __future__ import annotations

import logging
import math
import re
from typing import Any

from backend.app.core.errors import DomainError

logger = logging.getLogger(__name__)


class ImageRequestMixin:
    def _resolve_initial_image_size(self, model_name: str) -> str:
        lowered = str(model_name or "").lower()
        if any(marker in lowered for marker in ("seedream", "seededit")):
            return "1920x1920"
        return "1024x1024"

    def _extract_required_min_pixels(self, error_message: str) -> int | None:
        match = re.search(r"at least\s+(\d+)\s+pixels", str(error_message or ""), flags=re.IGNORECASE)
        if not match:
            return None
        try:
            required = int(match.group(1))
        except ValueError:
            return None
        return required if required > 0 else None

    def _build_square_size_from_min_pixels(self, min_pixels: int) -> str:
        edge = int(math.ceil(math.sqrt(max(min_pixels, 1))))
        return f"{edge}x{edge}"

    def _request_image_generation_payload(
        self,
        *,
        provider_id: str,
        model_name: str,
        endpoint: str,
        api_key: str,
        prompt: str,
        reference_image_paths: list[str] | None,
    ) -> dict[str, Any]:
        current_size = self._resolve_initial_image_size(model_name)
        payload = self._build_image_generation_payload(
            model_name=model_name,
            prompt=prompt,
            reference_image_paths=reference_image_paths,
            size=current_size,
        )
        try:
            return self._post_json(
                provider_id=provider_id,
                model_name=model_name,
                url=endpoint,
                api_key=api_key,
                payload=payload,
            )
        except DomainError as error:
            retry_error = error
            required_min_pixels = self._extract_required_min_pixels(error.message)
            if required_min_pixels:
                current_size = self._build_square_size_from_min_pixels(required_min_pixels)
                logger.warning(
                    "生图尺寸自适应重试: provider_id=%s model_name=%s size=%s reason=%s",
                    provider_id,
                    model_name,
                    current_size,
                    error.message,
                )
                retry_payload = self._build_image_generation_payload(
                    model_name=model_name,
                    prompt=prompt,
                    reference_image_paths=reference_image_paths,
                    size=current_size,
                )
                try:
                    return self._post_json(
                        provider_id=provider_id,
                        model_name=model_name,
                        url=endpoint,
                        api_key=api_key,
                        payload=retry_payload,
                    )
                except DomainError as size_error:
                    retry_error = size_error
            if reference_image_paths and self._should_retry_without_references(retry_error):
                logger.warning(
                    "生图参考图参数不兼容，自动回退 prompt-only: provider_id=%s model_name=%s reason=%s",
                    provider_id,
                    model_name,
                    retry_error.message,
                )
                fallback_payload = self._build_image_generation_payload(
                    model_name=model_name,
                    prompt=prompt,
                    reference_image_paths=None,
                    size=current_size,
                )
                return self._post_json(
                    provider_id=provider_id,
                    model_name=model_name,
                    url=endpoint,
                    api_key=api_key,
                    payload=fallback_payload,
                )
            raise retry_error
