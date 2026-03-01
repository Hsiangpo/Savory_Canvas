from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.app.core.errors import DomainError


class StylePayloadMixin:
    def _normalize_style_payload(self, style_payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(style_payload, dict):
            raise DomainError(code="E-1099", message="style_payload 结构不合法", status_code=400)
        legacy_prompt_example = self._coerce_text(style_payload.get("background_decor"), default="")
        painting_style = self._coerce_text(style_payload.get("painting_style"), default="手绘插画")
        color_mood = self._coerce_text(style_payload.get("color_mood"), default="温暖治愈")
        prompt_example = self._coerce_text(
            style_payload.get("prompt_example"),
            default=legacy_prompt_example or "请保持统一风格与清晰图文布局。",
        )
        style_prompt = self._coerce_text(style_payload.get("style_prompt"), default=prompt_example)
        sample_image_asset_ids = self._normalize_sample_image_asset_ids(
            sample_image_asset_id=style_payload.get("sample_image_asset_id"),
            sample_image_asset_ids=style_payload.get("sample_image_asset_ids"),
        )
        sample_image_file_paths = style_payload.get("sample_image_file_paths")
        if sample_image_file_paths is not None and not isinstance(sample_image_file_paths, list):
            raise DomainError(code="E-1099", message="sample_image_file_paths 不合法", status_code=400)
        extra_keywords = style_payload.get("extra_keywords")
        if extra_keywords is None:
            extra_keywords = []
        if not isinstance(extra_keywords, list):
            raise DomainError(code="E-1099", message="extra_keywords 必须为数组", status_code=400)
        normalized_keywords = self._normalize_keyword_list(extra_keywords)
        normalized_payload = {
            "painting_style": painting_style,
            "color_mood": color_mood,
            "prompt_example": prompt_example,
            "style_prompt": style_prompt,
            "sample_image_asset_id": sample_image_asset_ids[0] if sample_image_asset_ids else None,
            "sample_image_asset_ids": sample_image_asset_ids,
            "extra_keywords": normalized_keywords,
        }
        normalized_file_paths = self._normalize_sample_image_file_paths(sample_image_file_paths)
        if normalized_file_paths:
            normalized_payload["sample_image_file_paths"] = normalized_file_paths
            normalized_payload["sample_image_file_path"] = normalized_file_paths[0]
        if isinstance(style_payload.get("force_partial_fail"), bool):
            normalized_payload["force_partial_fail"] = style_payload["force_partial_fail"]
        if isinstance(style_payload.get("image_count"), (int, str)):
            normalized_payload["image_count"] = style_payload["image_count"]
        if isinstance(style_payload.get("draft_style_id"), str):
            normalized_payload["draft_style_id"] = style_payload["draft_style_id"]
        if "allocation_plan" in style_payload:
            normalized_payload["allocation_plan"] = self._normalize_allocation_plan(style_payload.get("allocation_plan"))
        return normalized_payload

    def _normalize_sample_image_asset_ids(
        self,
        *,
        sample_image_asset_id: Any,
        sample_image_asset_ids: Any,
    ) -> list[str]:
        raw_values: list[Any] = []
        if isinstance(sample_image_asset_ids, list):
            raw_values.extend(sample_image_asset_ids)
        elif sample_image_asset_ids is not None:
            raise DomainError(code="E-1099", message="sample_image_asset_ids 不合法", status_code=400)
        if sample_image_asset_id is not None:
            raw_values.append(sample_image_asset_id)
        normalized: list[str] = []
        seen: set[str] = set()
        for value in raw_values:
            if not isinstance(value, str):
                raise DomainError(code="E-1099", message="sample_image_asset_id 不合法", status_code=400)
            asset_id = value.strip()
            if not asset_id or asset_id in seen:
                continue
            seen.add(asset_id)
            normalized.append(asset_id)
        return normalized[:10]

    def _normalize_sample_image_file_paths(self, values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                continue
            file_path = value.strip()
            if not file_path or file_path in seen:
                continue
            seen.add(file_path)
            normalized.append(file_path)
        return normalized[:10]

    def _coerce_text(self, value: Any, *, default: str) -> str:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        if isinstance(value, list):
            merged = "、".join(str(item).strip() for item in value if str(item).strip())
            if merged:
                return merged
        return default

    def _normalize_keyword_list(self, values: list[Any]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized

    def _normalize_allocation_plan(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized_items: list[dict[str, Any]] = []
        for item in value[:10]:
            if not isinstance(item, dict):
                continue
            slot_index_raw = item.get("slot_index")
            slot_index = int(slot_index_raw) if isinstance(slot_index_raw, (int, float, str)) and str(slot_index_raw).isdigit() else 0
            if slot_index <= 0:
                continue
            focus_title = str(item.get("focus_title") or "").strip()
            focus_description = str(item.get("focus_description") or "").strip()
            if not focus_description:
                continue
            normalized_items.append(
                {
                    "slot_index": slot_index,
                    "focus_title": focus_title or f"第{slot_index}张重点",
                    "focus_description": focus_description,
                    "locations": self._normalize_keyword_list(item.get("locations") if isinstance(item.get("locations"), list) else []),
                    "scenes": self._normalize_keyword_list(item.get("scenes") if isinstance(item.get("scenes"), list) else []),
                    "foods": self._normalize_keyword_list(item.get("foods") if isinstance(item.get("foods"), list) else []),
                    "keywords": self._normalize_keyword_list(item.get("keywords") if isinstance(item.get("keywords"), list) else []),
                    "source_asset_ids": self._normalize_keyword_list(
                        item.get("source_asset_ids") if isinstance(item.get("source_asset_ids"), list) else []
                    ),
                    "confirmed": bool(item.get("confirmed")),
                }
            )
        normalized_items.sort(key=lambda plan_item: int(plan_item.get("slot_index") or 0))
        return normalized_items

    def _enrich_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        payload = self._normalize_style_payload(dict(profile.get("style_payload") or {}))
        self._validate_sample_image_assets(
            session_id=profile.get("session_id"),
            style_payload=payload,
            strict=False,
        )
        sample_image_preview_urls = self._resolve_sample_image_preview_urls(payload)
        sample_image_preview_url = sample_image_preview_urls[0] if sample_image_preview_urls else None
        return {
            **profile,
            "style_payload": payload,
            "sample_image_preview_url": sample_image_preview_url,
            "sample_image_preview_urls": sample_image_preview_urls,
        }

    def _sync_sample_image_snapshot(
        self,
        style_payload: dict[str, Any],
        previous_payload: dict[str, Any] | None,
    ) -> None:
        sample_asset_ids = style_payload.get("sample_image_asset_ids")
        if not isinstance(sample_asset_ids, list):
            sample_asset_ids = []
        resolved_paths = self._resolve_sample_image_file_paths_from_assets(sample_asset_ids)
        if resolved_paths:
            style_payload["sample_image_file_paths"] = resolved_paths
            style_payload["sample_image_file_path"] = resolved_paths[0]
            return
        if not previous_payload:
            style_payload.pop("sample_image_file_path", None)
            style_payload.pop("sample_image_file_paths", None)
            return
        previous_ids = previous_payload.get("sample_image_asset_ids")
        if not isinstance(previous_ids, list):
            previous_ids = []
        previous_paths = previous_payload.get("sample_image_file_paths")
        if not isinstance(previous_paths, list):
            previous_path = previous_payload.get("sample_image_file_path")
            previous_paths = [previous_path] if isinstance(previous_path, str) and previous_path.strip() else []
        if sample_asset_ids == previous_ids and previous_paths:
            style_payload["sample_image_file_paths"] = previous_paths
            style_payload["sample_image_file_path"] = previous_paths[0]
            return
        style_payload.pop("sample_image_file_path", None)
        style_payload.pop("sample_image_file_paths", None)

    def _resolve_sample_image_file_paths_from_assets(self, asset_ids: list[str]) -> list[str]:
        if not asset_ids or not self.asset_repo:
            return []
        resolved: list[str] = []
        seen: set[str] = set()
        for asset_id in asset_ids:
            if not isinstance(asset_id, str) or not asset_id.strip():
                continue
            asset = self.asset_repo.get(asset_id)
            if not asset or asset.get("asset_type") != "image":
                continue
            file_path = asset.get("file_path")
            if not isinstance(file_path, str):
                continue
            normalized = file_path.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            resolved.append(normalized)
        return resolved

    def _validate_sample_image_assets(
        self,
        *,
        session_id: str | None,
        style_payload: dict[str, Any],
        strict: bool,
    ) -> None:
        sample_image_asset_ids = style_payload.get("sample_image_asset_ids")
        if not isinstance(sample_image_asset_ids, list):
            sample_image_asset_ids = []
        if not self.asset_repo:
            if strict:
                raise DomainError(code="E-1099", message="样例图校验服务不可用", status_code=400)
            style_payload["sample_image_asset_id"] = None
            style_payload["sample_image_asset_ids"] = []
            return
        valid_ids: list[str] = []
        for asset_id in sample_image_asset_ids:
            if not isinstance(asset_id, str) or not asset_id.strip():
                continue
            asset = self.asset_repo.get(asset_id)
            if not asset or asset.get("asset_type") != "image":
                if strict:
                    raise DomainError(code="E-1099", message="样例图必须绑定有效的图片素材", status_code=400)
                continue
            valid_ids.append(asset_id.strip())
        if strict and sample_image_asset_ids and not valid_ids:
            raise DomainError(code="E-1099", message="样例图必须绑定有效的图片素材", status_code=400)
        style_payload["sample_image_asset_ids"] = valid_ids
        style_payload["sample_image_asset_id"] = valid_ids[0] if valid_ids else None

    def _resolve_sample_image_preview_urls(self, source: Any) -> list[str]:
        payload = source if isinstance(source, dict) else {}
        result: list[str] = []
        seen: set[str] = set()
        file_paths = payload.get("sample_image_file_paths") if isinstance(payload, dict) else None
        if isinstance(file_paths, list):
            for file_path in file_paths:
                preview_url = self._build_public_image_url(file_path)
                if preview_url and preview_url not in seen:
                    seen.add(preview_url)
                    result.append(preview_url)
        legacy_file_path = payload.get("sample_image_file_path") if isinstance(payload, dict) else None
        if isinstance(legacy_file_path, str):
            preview_url = self._build_public_image_url(legacy_file_path)
            if preview_url and preview_url not in seen:
                seen.add(preview_url)
                result.append(preview_url)
        asset_ids = payload.get("sample_image_asset_ids") if isinstance(payload, dict) else None
        if not isinstance(asset_ids, list):
            asset_id = source if isinstance(source, str) else payload.get("sample_image_asset_id")
            asset_ids = [asset_id] if isinstance(asset_id, str) and asset_id.strip() else []
        if self.asset_repo:
            for asset_id in asset_ids:
                if not isinstance(asset_id, str) or not asset_id.strip():
                    continue
                asset = self.asset_repo.get(asset_id)
                if not asset or asset.get("asset_type") != "image":
                    continue
                preview_url = self._build_public_image_url(asset.get("file_path"))
                if preview_url and preview_url not in seen:
                    seen.add(preview_url)
                    result.append(preview_url)
        return result[:10]

    def _resolve_sample_image_preview_url(self, source: Any) -> str | None:
        preview_urls = self._resolve_sample_image_preview_urls(source)
        if preview_urls:
            return preview_urls[0]
        return None

    def _build_public_image_url(self, file_path: Any) -> str | None:
        if not isinstance(file_path, str) or not file_path.strip() or not self.storage or not self.public_base_url:
            return None
        normalized = file_path.replace("\\", "/")
        if normalized.startswith("http://") or normalized.startswith("https://"):
            return normalized
        if normalized.startswith("images/") or normalized.startswith("generated/"):
            relative = normalized
        else:
            try:
                resolved = Path(file_path).resolve()
                relative = resolved.relative_to(self.storage.base_dir.resolve()).as_posix()
            except Exception:
                relative = normalized
        return f"{self.public_base_url}/static/{relative.lstrip('/')}"
