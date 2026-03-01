from __future__ import annotations

import re


def build_text_model_name_candidates(model_name: str) -> list[str]:
    """构建文本模型候选名，优先原模型，必要时回退到基础模型。"""
    primary = str(model_name or "").strip()
    if not primary:
        return [primary]
    candidates = [primary]
    lowered = primary.lower()
    if "thinking" not in lowered:
        return candidates
    fallback = re.sub(r"[-_]?thinking[-_a-z0-9]*$", "", primary, flags=re.IGNORECASE).strip("-_ ")
    if fallback and fallback not in candidates:
        candidates.append(fallback)
    return candidates


def should_retry_text_model_name(reason: str | None, detail: str | None) -> bool:
    """判断是否应切换到基础模型名重试。"""
    if reason != "upstream_http_error":
        return False
    lowered = str(detail or "").lower()
    return "thinking budget" in lowered and "thinking level" in lowered
