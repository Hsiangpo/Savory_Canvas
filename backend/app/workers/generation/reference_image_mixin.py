from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from backend.app.core.errors import DomainError
from backend.app.infra.http_client import HttpClientHttpError, HttpClientNetworkError, download_binary
from backend.app.workers.generation.image_postprocess import postprocess_generated_image


IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 90


class GenerationReferenceImageMixin:
    def _build_image_generation_payload(
        self,
        *,
        model_name: str,
        prompt: str,
        reference_image_paths: list[str] | None,
        size: str | None = None,
    ) -> dict[str, Any]:
        resolved_size = size or self._resolve_initial_image_size(model_name)
        payload: dict[str, Any] = {
            "model": model_name,
            "prompt": prompt,
            "size": resolved_size,
            "n": 1,
            "response_format": "b64_json",
        }
        reference_inputs = self._build_reference_inputs(reference_image_paths or [])
        if reference_inputs:
            payload["input_images"] = reference_inputs
            payload["images"] = reference_inputs
            if len(reference_inputs) == 1:
                payload["image"] = reference_inputs[0]
        return payload

    def _build_reference_inputs(self, reference_image_paths: list[str]) -> list[str]:
        normalized_inputs: list[str] = []
        seen: set[str] = set()
        for path in reference_image_paths[:3]:
            serialized = self._serialize_reference_image(path)
            if not serialized or serialized in seen:
                continue
            seen.add(serialized)
            normalized_inputs.append(serialized)
        return normalized_inputs

    def _serialize_reference_image(self, path: str) -> str | None:
        if not isinstance(path, str):
            return None
        normalized = path.strip()
        if not normalized:
            return None
        lowered = normalized.lower()
        if lowered.startswith("http://") or lowered.startswith("https://") or lowered.startswith("data:image/"):
            return normalized
        local_path = self._resolve_storage_path(normalized)
        if local_path is None:
            return None
        try:
            image_bytes = local_path.read_bytes()
        except OSError:
            return None
        extension = self._detect_image_extension(image_bytes) or local_path.suffix.lstrip(".").lower()
        mime = self._map_extension_to_mime(extension)
        if not mime:
            return None
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _map_extension_to_mime(self, extension: str) -> str:
        mapping = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
            "gif": "image/gif",
            "avif": "image/avif",
        }
        return mapping.get(extension.lower(), "")

    def _resolve_storage_path(self, path: str) -> Path | None:
        raw_path = Path(path)
        if raw_path.is_file():
            return raw_path
        normalized = path.replace("\\", "/").lstrip("/")
        if normalized.startswith("static/"):
            normalized = normalized[len("static/") :]
        candidate = self.storage.base_dir / normalized
        if candidate.is_file():
            return candidate
        return None

    def _collect_style_reference_paths(
        self,
        *,
        session_id: str,
        source_assets: list[dict[str, Any]],
        style_payload: dict[str, Any],
        allow_image_reference: bool,
    ) -> list[str]:
        if not allow_image_reference:
            return []
        references: list[str] = []
        source_assets_by_id = {asset.get("id"): asset for asset in source_assets if isinstance(asset.get("id"), str)}
        tagged_reference_asset_ids = self._collect_tagged_reference_asset_ids(session_id)
        sample_asset_ids = style_payload.get("sample_image_asset_ids")
        if isinstance(sample_asset_ids, list):
            for sample_asset_id in sample_asset_ids:
                if not isinstance(sample_asset_id, str) or not sample_asset_id.strip():
                    continue
                sample_asset = self.asset_repo.get(sample_asset_id.strip())
                sample_path = sample_asset.get("file_path") if sample_asset else None
                if isinstance(sample_path, str) and sample_path.strip():
                    references.append(sample_path.strip())
        else:
            sample_asset_id = style_payload.get("sample_image_asset_id")
            if isinstance(sample_asset_id, str) and sample_asset_id.strip():
                sample_asset = self.asset_repo.get(sample_asset_id.strip())
                sample_path = sample_asset.get("file_path") if sample_asset else None
                if isinstance(sample_path, str) and sample_path.strip():
                    references.append(sample_path.strip())
        sample_file_path = style_payload.get("sample_image_file_path")
        if isinstance(sample_file_path, str) and sample_file_path.strip():
            references.append(sample_file_path.strip())
        sample_file_paths = style_payload.get("sample_image_file_paths")
        if isinstance(sample_file_paths, list):
            for sample_path in sample_file_paths:
                if isinstance(sample_path, str) and sample_path.strip():
                    references.append(sample_path.strip())
        for asset_id in tagged_reference_asset_ids:
            asset = source_assets_by_id.get(asset_id) or self.asset_repo.get(asset_id)
            if not asset or asset.get("asset_type") != "image":
                continue
            asset_path = asset.get("file_path")
            if isinstance(asset_path, str) and asset_path.strip():
                references.append(asset_path.strip())
        deduplicated: list[str] = []
        seen: set[str] = set()
        for item in references:
            if item in seen:
                continue
            seen.add(item)
            deduplicated.append(item)
        return deduplicated

    def _collect_tagged_reference_asset_ids(self, session_id: str) -> list[str]:
        tagged_ids: list[str] = []
        seen: set[str] = set()
        for message in self.inspiration_repo.list_messages(session_id):
            attachments = message.get("attachments") or []
            if not isinstance(attachments, list):
                continue
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                if attachment.get("type") != "image":
                    continue
                if attachment.get("usage_type") != "style_reference":
                    continue
                asset_id = attachment.get("asset_id") or attachment.get("id")
                if not isinstance(asset_id, str) or not asset_id.strip():
                    continue
                if asset_id in seen:
                    continue
                seen.add(asset_id)
                tagged_ids.append(asset_id)
        return tagged_ids

    def _looks_like_style_reference_asset(self, asset: dict[str, Any]) -> bool:
        name = str(asset.get("content") or "").lower()
        markers = ("style", "reference", "ref", "样例", "参考", "风格", "模板")
        return any(marker in name for marker in markers)

    def _build_reference_chain_for_slot(
        self,
        *,
        slot: int,
        base_reference_paths: list[str],
        results_by_slot: dict[int, dict[str, Any]],
        allow_image_reference: bool,
    ) -> list[str]:
        if not allow_image_reference:
            return []
        chain = list(base_reference_paths)
        if slot > 0:
            previous_result = results_by_slot.get(slot - 1)
            previous_path = previous_result.get("image_path") if previous_result else None
            if isinstance(previous_path, str) and previous_path.strip():
                chain.append(previous_path.strip())
        deduplicated: list[str] = []
        seen: set[str] = set()
        for item in chain:
            if item in seen:
                continue
            seen.add(item)
            deduplicated.append(item)
        return deduplicated

    def _should_retry_without_references(self, error: DomainError) -> bool:
        message = error.message.lower()
        retry_markers = (
            "unsupported",
            "not support",
            "unknown",
            "invalid image",
            "input_images",
            "reference",
            "不支持",
            "参考图",
            "参数",
        )
        return any(marker in message for marker in retry_markers)

    def _extract_primary_image_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        data_items = payload.get("data")
        if isinstance(data_items, list) and data_items:
            first_item = data_items[0]
            if isinstance(first_item, dict):
                return first_item
            if isinstance(first_item, str):
                trimmed = first_item.strip()
                if trimmed.startswith(("http://", "https://", "data:image/")):
                    return {"url": trimmed}
                return {"base64": trimmed}
        candidate_keys = {
            "b64_json",
            "image_base64",
            "base64",
            "data",
            "url",
            "image_url",
            "download_url",
            "image",
            "output",
        }
        if any(key in payload for key in candidate_keys):
            return payload
        raise DomainError(code="E-1004", message="上游未返回图片结果", status_code=400)

    def _decode_base64_image(self, value: Any) -> tuple[bytes, str] | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        if text.startswith("data:"):
            comma_index = text.find(",")
            if comma_index < 0:
                return None
            header = text[:comma_index].lower()
            if "base64" not in header:
                return None
            text = text[comma_index + 1 :].strip()
        try:
            image_bytes = base64.b64decode(text, validate=False)
        except Exception:
            return None
        if not image_bytes:
            return None
        extension = self._detect_image_extension(image_bytes)
        if not extension:
            return None
        return image_bytes, extension

    def _download_binary(
        self,
        *,
        provider_id: str,
        model_name: str,
        endpoint: str,
    ) -> bytes:
        try:
            return download_binary(endpoint, timeout=IMAGE_DOWNLOAD_TIMEOUT_SECONDS)
        except HttpClientHttpError as error:
            self._log_upstream_failure(
                provider_id=provider_id,
                model_name=model_name,
                endpoint=endpoint,
                http_status=error.status_code,
                reason="download_http_error",
            )
            raise DomainError(code="E-1004", message=f"图片下载失败：HTTP {error.status_code}", status_code=400) from error
        except HttpClientNetworkError as error:
            self._log_upstream_failure(
                provider_id=provider_id,
                model_name=model_name,
                endpoint=endpoint,
                http_status=None,
                reason="download_error:network",
            )
            raise DomainError(code="E-1004", message="图片下载失败", status_code=400) from error

    def _infer_extension_from_url(self, url: str) -> str:
        cleaned = url.split("?")[0].split("#")[0].lower()
        if cleaned.endswith(".jpg") or cleaned.endswith(".jpeg"):
            return "jpg"
        if cleaned.endswith(".webp"):
            return "webp"
        if cleaned.endswith(".gif"):
            return "gif"
        if cleaned.endswith(".avif"):
            return "avif"
        if cleaned.endswith(".png"):
            return "png"
        return ""

    def _detect_image_extension(self, content: bytes) -> str:
        if len(content) < 12:
            return ""
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if content.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
            return "gif"
        if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return "webp"
        if content[4:12] == b"ftypavif":
            return "avif"
        return ""

    def _postprocess_generated_image(self, image_bytes: bytes, extension: str) -> tuple[bytes, str]:
        return postprocess_generated_image(image_bytes, extension)
