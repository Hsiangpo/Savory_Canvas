from __future__ import annotations

import logging
from typing import Any

from backend.app.infra import http_client as http_client_module
from backend.app.workers.generation.copy_generation_mixin import CopyModelError, GenerationCopyGenerationMixin
from backend.app.workers.generation.image_request_mixin import ImageRequestMixin
from backend.app.workers.generation.reference_image_mixin import GenerationReferenceImageMixin
from backend.app.workers.generation.upstream_error_mixin import GenerationUpstreamErrorMixin

NON_VISUAL_STYLE_KEYS = {"image_count", "style_prompt", "force_partial_fail", "draft_style_id", "allocation_plan"}
logger = logging.getLogger(__name__)
request = http_client_module.request


class GenerationPipelineMixin(
    GenerationCopyGenerationMixin,
    GenerationUpstreamErrorMixin,
    GenerationReferenceImageMixin,
    ImageRequestMixin,
):
    # Keep this file as a thin facade so GenerationWorker can import one stable mixin
    # while detailed protocol / reference / copy logic lives in dedicated modules.
    _logger = logger

    def _format_style_payload(self, style_payload: dict[str, Any]) -> str:
        if not style_payload:
            return "保持自然写实风格。"
        segments: list[str] = []
        for key, value in style_payload.items():
            if self._is_non_visual_style_key(key):
                continue
            if isinstance(value, list):
                text_value = "、".join(str(item) for item in value if str(item).strip())
            else:
                text_value = str(value).strip()
            if text_value:
                segments.append(f"{key}：{text_value}")
        if not segments:
            return "保持自然写实风格。"
        return "；".join(segments) + "。"

    def _is_non_visual_style_key(self, key: str) -> bool:
        lowered = key.lower().strip()
        if lowered in NON_VISUAL_STYLE_KEYS:
            return True
        if lowered.endswith("_count"):
            return True
        return False

    def _calc_image_progress(self, *, index: int, total: int) -> int:
        if total <= 0:
            return 60
        span = 20
        progress = 60 + int((index - 1) * span / total)
        return min(max(progress, 60), 80)
