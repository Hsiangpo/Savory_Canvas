from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib import error as url_error
from urllib import request

from backend.app.core.errors import DomainError, not_found
from backend.app.core.utils import now_iso
from backend.app.repositories.config_repo import ConfigRepository
from backend.app.repositories.provider_repo import ProviderRepository

logger = logging.getLogger(__name__)


class ModelService:
    def __init__(self, config_repo: ConfigRepository, provider_repo: ProviderRepository):
        self.config_repo = config_repo
        self.provider_repo = provider_repo
        self._provider_model_cache: dict[str, list[dict[str, Any]]] = {}

    def list_models(self, provider_id: str) -> dict[str, Any]:
        provider = self.provider_repo.get(provider_id)
        if not provider:
            raise not_found("提供商", provider_id)
        return {"provider_id": provider_id, "items": self.fetch_provider_models(provider)}

    def get_routing(self) -> dict[str, Any] | None:
        return self.config_repo.get_model_routing()

    def require_routing(self) -> dict[str, Any]:
        routing = self.get_routing()
        if not routing:
            raise DomainError(code="E-1006", message="请先完成模型设置", status_code=400)
        return routing

    def update_routing(self, payload: dict[str, Any]) -> dict[str, Any]:
        image_provider = self.provider_repo.get(payload["image_model"]["provider_id"])
        text_provider = self.provider_repo.get(payload["text_model"]["provider_id"])

        if not image_provider or not image_provider["enabled"]:
            raise DomainError(code="E-1006", message="图片模型提供商不可用", status_code=400)
        if not text_provider or not text_provider["enabled"]:
            raise DomainError(code="E-1006", message="文字模型提供商不可用", status_code=400)

        image_models = self.fetch_provider_models(image_provider)
        if image_provider["id"] == text_provider["id"]:
            text_models = image_models
        else:
            text_models = self.fetch_provider_models(text_provider)

        image_model = self._find_model(image_models, payload["image_model"]["model_name"])
        text_model = self._find_model(text_models, payload["text_model"]["model_name"])

        if not image_model:
            raise DomainError(code="E-1006", message="图片模型不存在", status_code=400)
        if not text_model:
            raise DomainError(code="E-1006", message="文字模型不存在", status_code=400)
        if "image_generation" not in image_model["capabilities"]:
            raise DomainError(code="E-1006", message="图片模型必须具备 image_generation 能力", status_code=400)
        if "text_generation" not in text_model["capabilities"]:
            raise DomainError(code="E-1006", message="文字模型必须具备 text_generation 能力", status_code=400)

        return self.config_repo.upsert_model_routing(payload, updated_at=now_iso())

    def fetch_provider_models(self, provider: dict[str, Any]) -> list[dict[str, Any]]:
        endpoints = self._build_model_list_endpoints(provider["base_url"])
        request_headers = {
            "Authorization": f"Bearer {provider['api_key']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        }
        last_error: DomainError | None = None
        for endpoint in endpoints:
            upstream_request = request.Request(
                url=endpoint,
                method="GET",
                headers=request_headers,
            )
            for _ in range(3):
                try:
                    with request.urlopen(upstream_request, timeout=10) as response:
                        body_text = response.read().decode("utf-8")
                except (url_error.URLError, TimeoutError, OSError):
                    last_error = DomainError(code="E-1006", message="上游模型列表获取失败", status_code=400)
                    continue

                try:
                    payload = json.loads(body_text)
                except json.JSONDecodeError:
                    last_error = DomainError(code="E-1006", message="上游模型列表响应格式错误", status_code=400)
                    break

                data_items = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data_items, list):
                    last_error = DomainError(code="E-1006", message="上游模型列表响应格式错误", status_code=400)
                    break

                models = self._normalize_model_items(data_items)
                if not models:
                    last_error = DomainError(code="E-1006", message="上游未返回可用模型", status_code=400)
                    break
                provider_id = str(provider.get("id") or "").strip()
                if provider_id:
                    self._provider_model_cache[provider_id] = models
                return models

        provider_id = str(provider.get("id") or "").strip()
        if provider_id and provider_id in self._provider_model_cache:
            logger.warning("上游模型列表拉取失败，使用缓存模型: provider_id=%s", provider_id)
            return self._provider_model_cache[provider_id]
        raise last_error or DomainError(code="E-1006", message="上游模型列表获取失败", status_code=400)

    def _build_model_list_endpoints(self, base_url: str) -> list[str]:
        normalized_base_url = base_url.strip().rstrip("/")
        if not normalized_base_url:
            return []

        endpoints: list[str] = [f"{normalized_base_url}/models"]
        if not normalized_base_url.lower().endswith("/v1"):
            endpoints.append(f"{normalized_base_url}/v1/models")
        return list(dict.fromkeys(endpoints))

    def _normalize_model_items(self, data_items: list[Any]) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for item in data_items:
            if not isinstance(item, dict):
                continue
            model_name = item.get("id")
            if not isinstance(model_name, str):
                continue
            normalized_name = model_name.strip()
            if not normalized_name or normalized_name in seen_names:
                continue
            seen_names.add(normalized_name)
            capabilities = self._merge_capabilities(
                self._extract_capabilities_from_upstream(item),
                self._infer_capabilities(normalized_name),
            )
            models.append(
                {
                    "id": normalized_name,
                    "name": normalized_name,
                    "capabilities": capabilities,
                }
            )
        return models

    def _find_model(self, models: list[dict[str, Any]], model_name: str) -> dict[str, Any] | None:
        for model in models:
            if model["name"] == model_name:
                return model
        return None

    def _infer_capabilities(self, model_name: str) -> list[str]:
        lowered = model_name.lower()
        if self._is_image_generation_model(lowered):
            return ["image_generation"]

        vision_markers = {
            "vision",
            "vl",
            "gpt-4o",
            "gpt-4.1",
            "gpt-5",
            "gemini",
            "claude",
            "pixtral",
            "llava",
            "janus",
            "qwen-vl",
            "multimodal",
        }
        capabilities = ["text_generation"]
        if any(marker in lowered for marker in vision_markers):
            capabilities.append("vision")
        return capabilities

    def _is_image_generation_model(self, lowered_model_name: str) -> bool:
        video_only_markers = {
            "t2v",
            "i2v",
            "v2v",
            "video",
            "seedance",
            "kling",
            "sora",
        }
        if any(marker in lowered_model_name for marker in video_only_markers):
            return False

        image_generation_markers = {
            "dall-e",
            "gpt-image",
            "nano-banana",
            "midjourney",
            "stable-diffusion",
            "sdxl",
            "flux",
            "imagen",
            "kandinsky",
            "qwen-image",
            "seedream",
            "seededit",
            "wanx-image",
            "cogview",
            "hunyuan-image",
        }
        if any(marker in lowered_model_name for marker in image_generation_markers):
            return True

        if re.search(r"(^|[-_/])(t2i|i2i|txt2img|img2img)([-_/]|$)", lowered_model_name):
            return True
        if "text-to-image" in lowered_model_name or "image-to-image" in lowered_model_name:
            return True
        if "image-edit" in lowered_model_name or "image_edit" in lowered_model_name:
            return True
        return False

    def _merge_capabilities(self, upstream_capabilities: list[str], inferred_capabilities: list[str]) -> list[str]:
        merged = set(upstream_capabilities) | set(inferred_capabilities)
        order = ["image_generation", "text_generation", "vision"]
        return [name for name in order if name in merged]

    def _extract_capabilities_from_upstream(self, item: dict[str, Any]) -> list[str]:
        raw_values: list[Any] = []
        for field_name in (
            "capabilities",
            "ability",
            "abilities",
            "features",
            "modalities",
            "input_modalities",
            "output_modalities",
        ):
            value = item.get(field_name)
            if value is not None:
                raw_values.append(value)

        if item.get("supports_image_generation") is True:
            raw_values.append("image_generation")
        if item.get("supports_text_generation") is True:
            raw_values.append("text_generation")
        if item.get("supports_vision") is True:
            raw_values.append("vision")

        token_set = self._normalize_capability_tokens(raw_values)
        if not token_set:
            return []

        capability_order = [
            (
                "image_generation",
                (
                    "image_generation",
                    "image",
                    "images",
                    "text_to_image",
                    "txt2img",
                    "text2image",
                    "image_to_image",
                    "img2img",
                    "image_edit",
                    "inpainting",
                    "outpainting",
                ),
            ),
            ("text_generation", ("text_generation", "text-generation", "text", "chat", "completion", "completions", "responses")),
            ("vision", ("vision", "multimodal", "image_understanding", "vl")),
        ]

        capabilities: list[str] = []
        for capability, markers in capability_order:
            if any(marker in token_set for marker in markers):
                capabilities.append(capability)
        return capabilities

    def _normalize_capability_tokens(self, raw_values: list[Any]) -> set[str]:
        tokens: set[str] = set()
        for value in raw_values:
            for text in self._flatten_capability_values(value):
                normalized = text.strip().lower().replace(" ", "_").replace("-", "_")
                if normalized:
                    tokens.add(normalized)
        return tokens

    def _flatten_capability_values(self, value: Any) -> list[str]:
        if isinstance(value, str):
            pieces = [piece for piece in value.replace("/", ",").split(",")]
            return [piece for piece in pieces if piece.strip()]
        if isinstance(value, (list, tuple, set)):
            flattened: list[str] = []
            for part in value:
                flattened.extend(self._flatten_capability_values(part))
            return flattened
        if isinstance(value, dict):
            flattened: list[str] = []
            for key, part in value.items():
                if bool(part):
                    flattened.extend(self._flatten_capability_values(key))
            return flattened
        return []
