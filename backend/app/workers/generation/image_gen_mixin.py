from __future__ import annotations

import asyncio
from typing import Any

from backend.app.core.errors import DomainError
from backend.app.core.utils import new_id, now_iso


class GenerationImageGenMixin:
    async def _generate_images(
        self,
        *,
        job: dict[str, Any],
        prompt_specs: list[dict[str, Any]],
        source_assets: list[dict[str, Any]],
        style: dict[str, Any],
        image_provider: dict[str, Any],
        image_model_name: str,
        allow_image_reference: bool,
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        results_by_slot: dict[int, dict[str, Any]] = {}
        failed_slots: set[int] = set()
        last_error_message: str | None = None
        force_partial = bool((style.get("style_payload") or {}).get("force_partial_fail"))
        total_images = len(prompt_specs)
        if total_images <= 0:
            return [], 0, None
        max_retry_per_slot = 4
        max_total_attempts = total_images * (max_retry_per_slot + 1)
        attempts_by_slot = [0] * total_images
        pending_slots = list(range(total_images))
        total_attempts = 0
        base_reference_paths = self._collect_style_reference_paths(
            session_id=job["session_id"],
            source_assets=source_assets,
            style_payload=style.get("style_payload") or {},
            allow_image_reference=allow_image_reference,
        )

        while pending_slots and total_attempts < max_total_attempts:
            if self._is_canceled(job["id"]):
                break
            slot = pending_slots.pop(0)
            if slot in results_by_slot or slot in failed_slots:
                continue
            attempts_by_slot[slot] += 1
            total_attempts += 1
            attempt = attempts_by_slot[slot]
            success_count = len(results_by_slot)
            progress = self._calc_image_progress(index=min(success_count + 1, total_images), total=total_images)
            self._advance(
                job["id"],
                "running",
                progress,
                "image_generate",
                f"正在生成图片（已成功 {success_count}/{total_images}，第 {attempt} 次尝试）",
            )
            await asyncio.sleep(0.06)

            if force_partial and slot == total_images - 1:
                last_error_message = "部分图片生成失败"
                if attempt <= max_retry_per_slot:
                    pending_slots.append(slot)
                else:
                    failed_slots.add(slot)
                continue
            try:
                spec = prompt_specs[slot]
                image_bytes, extension = self._generate_image_binary(
                    image_provider=image_provider,
                    provider_id=image_provider["id"],
                    model_name=image_model_name,
                    prompt=spec["prompt_text"],
                    reference_image_paths=self._select_reference_paths_for_attempt(
                        attempt=attempt,
                        slot=slot,
                        base_reference_paths=base_reference_paths,
                        results_by_slot=results_by_slot,
                        allow_image_reference=allow_image_reference,
                    ),
                )
            except DomainError as error:
                if last_error_message is None:
                    last_error_message = error.message
                else:
                    current_is_network_error = "网络异常" in error.message
                    previous_is_network_error = "网络异常" in last_error_message
                    if previous_is_network_error and not current_is_network_error:
                        last_error_message = error.message
                    elif previous_is_network_error == current_is_network_error:
                        last_error_message = error.message
                should_retry = self._is_retryable_image_failure(error)
                if should_retry and attempt <= max_retry_per_slot:
                    pending_slots.append(slot)
                else:
                    failed_slots.add(slot)
                continue

            image_index = slot + 1
            filename = f"{job['id']}_{image_index}.{extension}"
            self.storage.save_generated_image(filename=filename, content=image_bytes)
            image_relative_path = f"generated/{filename}"
            result = {
                "id": new_id(),
                "job_id": job["id"],
                "image_index": image_index,
                "asset_refs": spec.get("asset_refs") or [],
                "prompt_text": spec["prompt_text"],
                "image_path": image_relative_path,
                "created_at": now_iso(),
            }
            self.result_repo.add_image(result)
            results_by_slot[slot] = result

        for slot in range(total_images):
            if slot not in results_by_slot:
                failed_slots.add(slot)

        ordered_results = [results_by_slot[slot] for slot in sorted(results_by_slot.keys())]
        return ordered_results, len(failed_slots), last_error_message


    def _select_reference_paths_for_attempt(
        self,
        *,
        attempt: int,
        slot: int,
        base_reference_paths: list[str],
        results_by_slot: dict[int, dict[str, Any]],
        allow_image_reference: bool,
    ) -> list[str]:
        reference_paths = self._build_reference_chain_for_slot(
            slot=slot,
            base_reference_paths=base_reference_paths,
            results_by_slot=results_by_slot,
            allow_image_reference=allow_image_reference,
        )
        if attempt >= 3:
            return []
        if attempt == 2 and slot > 0:
            previous_result = results_by_slot.get(slot - 1)
            previous_path = previous_result.get("image_path") if previous_result else None
            if isinstance(previous_path, str) and previous_path.strip():
                return []
        return reference_paths

    def _is_retryable_image_failure(self, error: DomainError) -> bool:
        message = str(error.message or "").lower()
        non_retryable_markers = (
            "预扣费",
            "额度",
            "余额",
            "insufficient",
            "quota",
            "credit",
            "billing",
            "payment required",
            "api key",
            "unauthorized",
            "forbidden",
            "模型不存在",
            "model not found",
            "invalid model",
            "permission",
        )
        return not any(marker in message for marker in non_retryable_markers)

    def _resolve_image_model_provider(self) -> tuple[dict[str, Any], str, list[str]]:
        routing = self.model_service.require_routing()
        image_model = routing.get("image_model") or {}
        provider_id = image_model.get("provider_id")
        model_name = image_model.get("model_name")
        if not provider_id or not model_name:
            raise DomainError(code="E-1006", message="请先完成模型设置", status_code=400)
        provider = self.model_service.provider_repo.get(provider_id)
        if not provider or not provider.get("enabled"):
            raise DomainError(code="E-1006", message="图片模型提供商不可用", status_code=400)
        capabilities = self._resolve_image_model_capabilities(provider, model_name)
        return provider, model_name, capabilities

    def _resolve_image_model_capabilities(self, provider: dict[str, Any], model_name: str) -> list[str]:
        fallback_capabilities = ["image_generation"]
        try:
            provider_models = self.model_service.fetch_provider_models(provider)
        except DomainError as error:
            self._logger.warning(
                "读取模型能力失败，回退到本地规则: provider_id=%s model_name=%s reason=%s",
                provider.get("id"),
                model_name,
                error.message,
            )
            return fallback_capabilities
        for item in provider_models:
            if item.get("name") != model_name:
                continue
            raw_capabilities = item.get("capabilities")
            if isinstance(raw_capabilities, list):
                normalized = [str(capability).strip() for capability in raw_capabilities if str(capability).strip()]
                if normalized:
                    return normalized
            break
        return fallback_capabilities

    def _supports_image_reference(self, *, image_model_name: str, capabilities: list[str]) -> bool:
        capability_set = {str(item).strip().lower() for item in capabilities if str(item).strip()}
        if "vision" in capability_set:
            return True
        if "image_generation" not in capability_set:
            return False
        lowered_name = image_model_name.lower()
        if any(marker in lowered_name for marker in ("dall-e", "dalle")):
            return False
        image_reference_markers = (
            "gpt-image",
            "nano-banana",
            "flux",
            "stable-diffusion",
            "sdxl",
            "kandinsky",
            "img2img",
            "image-to-image",
            "janus",
        )
        return any(marker in lowered_name for marker in image_reference_markers)

    def _generate_image_binary(
        self,
        *,
        image_provider: dict[str, Any],
        provider_id: str,
        model_name: str,
        prompt: str,
        reference_image_paths: list[str] | None = None,
    ) -> tuple[bytes, str]:
        endpoint = f"{image_provider['base_url'].rstrip('/')}/images/generations"
        response_payload = self._request_image_generation_payload(
            provider_id=provider_id,
            model_name=model_name,
            endpoint=endpoint,
            api_key=image_provider["api_key"],
            prompt=prompt,
            reference_image_paths=reference_image_paths,
        )
        image_item = self._extract_primary_image_item(response_payload)

        b64_candidates = [
            image_item.get("b64_json"),
            image_item.get("image_base64"),
            image_item.get("base64"),
            image_item.get("data"),
            image_item.get("output"),
        ]
        for candidate in b64_candidates:
            decoded = self._decode_base64_image(candidate)
            if decoded:
                return self._postprocess_generated_image(decoded[0], decoded[1])

        url_candidates = [
            image_item.get("url"),
            image_item.get("image_url"),
            image_item.get("download_url"),
            image_item.get("image"),
        ]
        for url_candidate in url_candidates:
            if not isinstance(url_candidate, str) or not url_candidate.strip():
                continue
            image_url = url_candidate.strip()
            data_url_decoded = self._decode_base64_image(image_url)
            if image_url.startswith("data:image/"):
                if data_url_decoded:
                    return self._postprocess_generated_image(data_url_decoded[0], data_url_decoded[1])
                raise DomainError(code="E-1004", message="上游返回非图片内容", status_code=400)
            image_bytes = self._download_binary(
                provider_id=provider_id,
                model_name=model_name,
                endpoint=image_url,
            )
            extension = self._detect_image_extension(image_bytes) or self._infer_extension_from_url(image_url)
            if extension:
                return self._postprocess_generated_image(image_bytes, extension)
            raise DomainError(code="E-1004", message="上游返回非图片内容", status_code=400)

        has_image_like_data = any(isinstance(candidate, str) and candidate.strip() for candidate in b64_candidates)
        if has_image_like_data:
            raise DomainError(code="E-1004", message="上游返回非图片内容", status_code=400)
        raise DomainError(code="E-1004", message="上游未返回可用图片数据", status_code=400)
