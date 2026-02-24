from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib import error as url_error
from urllib import request

from backend.app.core.errors import DomainError
from backend.app.core.utils import new_id, now_iso
from backend.app.infra.storage import Storage
from backend.app.repositories.asset_repo import AssetRepository
from backend.app.repositories.inspiration_repo import InspirationRepository
from backend.app.repositories.job_repo import JobRepository
from backend.app.repositories.result_repo import ResultRepository
from backend.app.repositories.session_repo import SessionRepository
from backend.app.repositories.style_repo import StyleRepository
from backend.app.services.model_service import ModelService

FOOD_MARKERS = ("饭", "面", "汤", "鸡", "鸭", "鱼", "虾", "蟹", "肉", "牛", "羊", "猪", "茶", "咖啡", "甜点")
SCENE_MARKERS = ("海", "山", "寺", "街", "夜景", "日落", "晨光", "窗", "餐桌", "厨房", "花园", "湖", "森林")
NON_VISUAL_STYLE_KEYS = {"image_count", "style_prompt", "force_partial_fail", "draft_style_id"}
logger = logging.getLogger(__name__)


class CopyModelError(Exception):
    def __init__(self, reason: str, detail: str | None = None):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


class GenerationWorker:
    def __init__(
        self,
        job_repo: JobRepository,
        asset_repo: AssetRepository,
        inspiration_repo: InspirationRepository,
        style_repo: StyleRepository,
        result_repo: ResultRepository,
        session_repo: SessionRepository,
        model_service: ModelService,
        storage: Storage,
    ):
        self.job_repo = job_repo
        self.asset_repo = asset_repo
        self.inspiration_repo = inspiration_repo
        self.style_repo = style_repo
        self.result_repo = result_repo
        self.session_repo = session_repo
        self.model_service = model_service
        self.storage = storage

    def schedule(self, job_id: str) -> None:
        threading.Thread(target=lambda: asyncio.run(self._run(job_id)), daemon=True).start()

    async def _run(self, job_id: str) -> None:
        try:
            job = self._require_job(job_id)
            await asyncio.sleep(0.05)
            if self._is_canceled(job_id):
                return

            self._advance(job_id, "running", 8, "asset_extract", "正在提取素材")
            assets = self.asset_repo.list_by_session(job["session_id"])
            if not assets:
                self._fail(job_id, "E-1003", "素材不足，无法生成")
                return
            session = self.session_repo.get(job["session_id"])
            content_mode = (session or {}).get("content_mode") or "food"
            breakdown = self._build_asset_breakdown(job, assets, content_mode)
            self.result_repo.upsert_asset_breakdown(breakdown)
            self._complete_stage(job_id, progress=16, stage="asset_extract", stage_message="素材提取完成")
            await asyncio.sleep(0.04)

            if self._is_canceled(job_id):
                return
            self._advance(job_id, "running", 20, "asset_allocate", "正在分配素材")
            style = self.style_repo.get(job["style_profile_id"])
            if not style:
                self._fail(job_id, "E-2003", "风格不存在")
                return
            prompt_specs = self._build_prompt_specs(job["image_count"], breakdown, style, content_mode)
            self._complete_stage(job_id, progress=30, stage="asset_allocate", stage_message="素材分配完成")
            await asyncio.sleep(0.04)

            if self._is_canceled(job_id):
                return
            self._advance(job_id, "running", 35, "prompt_generate", "正在生成提示词")
            image_provider, image_model_name, image_model_capabilities = self._resolve_image_model_provider()
            self._complete_stage(job_id, progress=55, stage="prompt_generate", stage_message="提示词生成完成")
            await asyncio.sleep(0.04)

            if self._is_canceled(job_id):
                return
            self._advance(job_id, "running", 60, "image_generate", "正在生成图片")
            created_images, failed_images, last_error_message = await self._generate_images(
                job=job,
                prompt_specs=prompt_specs,
                source_assets=assets,
                style=style,
                image_provider=image_provider,
                image_model_name=image_model_name,
                allow_image_reference=self._supports_image_reference(
                    image_model_name=image_model_name,
                    capabilities=image_model_capabilities,
                ),
            )
            if not created_images:
                self._fail(job_id, "E-1004", last_error_message or "图片生成失败")
                return
            if failed_images > 0:
                image_stage_message = "图片生成完成，部分图片失败"
            else:
                image_stage_message = "图片生成完成"
            self._complete_stage(job_id, progress=80, stage="image_generate", stage_message=image_stage_message)

            if self._is_canceled(job_id):
                return
            self._advance(job_id, "running", 82, "copy_generate", "正在生成文案结构")
            copy_result = self._generate_copy_result(
                job=job,
                style=style,
                images=created_images,
                content_mode=content_mode,
                breakdown=breakdown,
            )
            self._advance(job_id, "running", 90, "copy_generate", "正在润色文案表达")
            self.result_repo.upsert_copy(copy_result)
            self._complete_stage(job_id, progress=92, stage="copy_generate", stage_message="文案生成完成")

            if self._is_canceled(job_id):
                return
            self._advance(job_id, "running", 95, "finalize", "正在整理结果")
            if failed_images > 0:
                self._finish(
                    job_id,
                    status="partial_success",
                    error_code="E-1004",
                    error_message="部分图片生成失败",
                    stage_message="任务完成，部分结果可用",
                )
            else:
                self._finish(
                    job_id,
                    status="success",
                    error_code=None,
                    error_message=None,
                    stage_message="任务完成",
                )
        except DomainError as error:
            self._fail(job_id, error.code, error.message)
        except Exception:
            self._fail(job_id, "E-1099", "生成流程异常")

    def _require_job(self, job_id: str) -> dict[str, Any]:
        job = self.job_repo.get(job_id)
        if not job:
            raise DomainError(code="E-2004", message="任务不存在", status_code=404)
        return job

    def _is_canceled(self, job_id: str) -> bool:
        job = self.job_repo.get(job_id)
        return bool(job and job["status"] == "canceled")

    def _advance(
        self,
        job_id: str,
        status: str,
        progress: int,
        stage: str,
        stage_message: str,
        error_code: str | None = None,
        error_message: str | None = None,
        log_status: str | None = None,
    ) -> None:
        self.job_repo.update_state_with_log(
            job_id=job_id,
            status=status,
            log_status=log_status,
            progress_percent=progress,
            current_stage=stage,
            stage_message=stage_message,
            error_code=error_code,
            error_message=error_message,
            updated_at=now_iso(),
            log_id=new_id(),
        )

    def _complete_stage(self, job_id: str, *, progress: int, stage: str, stage_message: str) -> None:
        self._advance(
            job_id=job_id,
            status="running",
            progress=progress,
            stage=stage,
            stage_message=stage_message,
            log_status="success",
        )

    def _build_asset_breakdown(
        self,
        job: dict[str, Any],
        assets: list[dict[str, Any]],
        content_mode: str,
    ) -> dict[str, Any]:
        source_assets = [
            {
                "asset_id": asset["id"],
                "asset_type": asset["asset_type"],
                "content": asset.get("content"),
            }
            for asset in assets
        ]
        foods: list[str] = []
        scenes: list[str] = []
        keywords: list[str] = []
        for source in source_assets:
            text = (source.get("content") or "").strip()
            if not text:
                continue
            self._append_unique(keywords, self._split_tokens(text))
            asset_type = source["asset_type"]
            if asset_type == "food_name":
                self._append_unique(foods, [text])
            elif asset_type == "scenic_name":
                self._append_unique(scenes, [text])
            elif asset_type in {"text", "transcript"}:
                token_foods, token_scenes = self._classify_tokens(self._split_tokens(text))
                self._append_unique(foods, token_foods)
                self._append_unique(scenes, token_scenes)

        if not foods and content_mode in {"food", "food_scenic"}:
            self._append_unique(foods, keywords[:2])
        if not scenes and content_mode in {"scenic", "food_scenic"}:
            self._append_unique(scenes, keywords[:2])

        return {
            "job_id": job["id"],
            "session_id": job["session_id"],
            "content_mode": content_mode,
            "source_assets": source_assets,
            "extracted": {
                "foods": foods[:10],
                "scenes": scenes[:10],
                "keywords": keywords[:15],
            },
            "created_at": now_iso(),
        }

    def _build_prompt_specs(
        self,
        image_count: int,
        breakdown: dict[str, Any],
        style: dict[str, Any],
        content_mode: str,
    ) -> list[dict[str, Any]]:
        style_description = self._format_style_payload(style.get("style_payload") or {})
        extracted = breakdown.get("extracted") or {}
        foods = extracted.get("foods") or []
        scenes = extracted.get("scenes") or []
        keywords = extracted.get("keywords") or []
        asset_refs = self._select_asset_refs(content_mode, breakdown.get("source_assets") or [])
        context_text = self._build_context_text(content_mode, foods, scenes, keywords)
        composition_variants = [
            "俯拍构图，主体居中，强调整体层次",
            "45 度近景，突出主体细节与质感",
            "平视中景，平衡主体与环境氛围",
            "特写镜头，聚焦关键纹理和光影",
            "低角度构图，增强画面立体感",
            "远景留白构图，突出叙事空间感",
        ]
        prompt_specs: list[dict[str, Any]] = []
        for index in range(1, image_count + 1):
            variant = composition_variants[(index - 1) % len(composition_variants)]
            prompt_text = (
                "请只生成一张图片。\n"
                f"内容要求：{context_text}\n"
                f"风格要求：{style_description}\n"
                f"构图差异化要求：{variant}。\n"
                "画面要求：主体清晰、细节完整、构图平衡。\n"
                "强约束：禁止拼贴、禁止九宫格、禁止分镜、禁止多画面合成、禁止任何文字水印。"
            )
            prompt_specs.append({"prompt_text": prompt_text, "asset_refs": list(asset_refs)})
        return prompt_specs

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
        max_retry_per_slot = 2
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
                    reference_image_paths=self._build_reference_chain_for_slot(
                        slot=slot,
                        base_reference_paths=base_reference_paths,
                        results_by_slot=results_by_slot,
                        allow_image_reference=allow_image_reference,
                    ),
                )
            except DomainError as error:
                last_error_message = error.message
                if attempt <= max_retry_per_slot:
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
            logger.warning(
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
        text_only_markers = ("dall-e", "dalle")
        if any(marker in lowered_name for marker in text_only_markers):
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
        payload = self._build_image_generation_payload(
            model_name=model_name,
            prompt=prompt,
            reference_image_paths=reference_image_paths,
        )
        try:
            response_payload = self._post_json(
                provider_id=provider_id,
                model_name=model_name,
                url=endpoint,
                api_key=image_provider["api_key"],
                payload=payload,
            )
        except DomainError as error:
            if reference_image_paths and self._should_retry_without_references(error):
                logger.warning(
                    "生图参考图参数不兼容，自动回退 prompt-only: provider_id=%s model_name=%s reason=%s",
                    provider_id,
                    model_name,
                    error.message,
                )
                fallback_payload = self._build_image_generation_payload(
                    model_name=model_name,
                    prompt=prompt,
                    reference_image_paths=None,
                )
                response_payload = self._post_json(
                    provider_id=provider_id,
                    model_name=model_name,
                    url=endpoint,
                    api_key=image_provider["api_key"],
                    payload=fallback_payload,
                )
            else:
                raise
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
                return decoded

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
                    return data_url_decoded
                raise DomainError(code="E-1004", message="上游返回非图片内容", status_code=400)
            image_bytes = self._download_binary(
                provider_id=provider_id,
                model_name=model_name,
                endpoint=image_url,
            )
            extension = self._detect_image_extension(image_bytes) or self._infer_extension_from_url(image_url)
            if extension:
                return image_bytes, extension
            raise DomainError(code="E-1004", message="上游返回非图片内容", status_code=400)

        has_image_like_data = any(isinstance(candidate, str) and candidate.strip() for candidate in b64_candidates)
        if has_image_like_data:
            raise DomainError(code="E-1004", message="上游返回非图片内容", status_code=400)
        raise DomainError(code="E-1004", message="上游未返回可用图片数据", status_code=400)

    def _build_image_generation_payload(
        self,
        *,
        model_name: str,
        prompt: str,
        reference_image_paths: list[str] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model_name,
            "prompt": prompt,
            "size": "1024x1024",
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
        source_assets_by_id = {
            asset.get("id"): asset
            for asset in source_assets
            if isinstance(asset.get("id"), str)
        }
        tagged_reference_asset_ids = self._collect_tagged_reference_asset_ids(session_id)
        sample_asset_id = style_payload.get("sample_image_asset_id")
        if isinstance(sample_asset_id, str) and sample_asset_id.strip():
            sample_asset = self.asset_repo.get(sample_asset_id.strip())
            sample_path = sample_asset.get("file_path") if sample_asset else None
            if isinstance(sample_path, str) and sample_path.strip():
                references.append(sample_path.strip())
        for asset_id in tagged_reference_asset_ids:
            asset = source_assets_by_id.get(asset_id) or self.asset_repo.get(asset_id)
            if not asset or asset.get("asset_type") != "image":
                continue
            asset_path = asset.get("file_path")
            if isinstance(asset_path, str) and asset_path.strip():
                references.append(asset_path.strip())
        for asset in source_assets:
            if asset.get("asset_type") != "image":
                continue
            file_path = asset.get("file_path")
            if not isinstance(file_path, str) or not file_path.strip():
                continue
            if self._looks_like_style_reference_asset(asset):
                references.append(file_path.strip())
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
        markers = (
            "style",
            "reference",
            "ref",
            "样例",
            "参考",
            "风格",
            "模板",
        )
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

    def _post_json(
        self,
        *,
        provider_id: str,
        model_name: str,
        url: str,
        api_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        upstream_request = request.Request(url=url, method="POST", headers=headers, data=body)
        try:
            with request.urlopen(upstream_request, timeout=45) as response:
                raw_text = response.read().decode("utf-8")
        except url_error.HTTPError as error:
            error_body_text = error.read().decode("utf-8", errors="ignore")
            upstream_error_message = self._extract_error_message_from_raw_text(error_body_text)
            self._log_upstream_failure(
                provider_id=provider_id,
                model_name=model_name,
                endpoint=url,
                http_status=error.code,
                reason="http_error",
            )
            if upstream_error_message:
                raise DomainError(
                    code="E-1004",
                    message=f"图片生成失败：{upstream_error_message}",
                    status_code=400,
                ) from error
            raise DomainError(code="E-1004", message=f"图片生成失败：HTTP {error.code}", status_code=400) from error
        except (url_error.URLError, TimeoutError, OSError) as error:
            self._log_upstream_failure(
                provider_id=provider_id,
                model_name=model_name,
                endpoint=url,
                http_status=None,
                reason=f"network_error:{type(error).__name__}",
            )
            raise DomainError(code="E-1004", message="图片生成失败：网络异常", status_code=400) from error

        try:
            parsed = json.loads(raw_text)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            self._log_upstream_failure(
                provider_id=provider_id,
                model_name=model_name,
                endpoint=url,
                http_status=200,
                reason="invalid_json",
            )
            raise DomainError(code="E-1004", message="图片生成失败：上游响应格式错误", status_code=400) from error
        if not isinstance(parsed, dict):
            self._log_upstream_failure(
                provider_id=provider_id,
                model_name=model_name,
                endpoint=url,
                http_status=200,
                reason="invalid_payload_type",
            )
            raise DomainError(code="E-1004", message="图片生成失败：上游响应格式错误", status_code=400)
        if self._is_explicit_upstream_error_payload(parsed):
            upstream_error_message = self._extract_upstream_error_message(parsed)
            self._log_upstream_failure(
                provider_id=provider_id,
                model_name=model_name,
                endpoint=url,
                http_status=200,
                reason="upstream_error_payload",
            )
            raise DomainError(
                code="E-1004",
                message=f"图片生成失败：{upstream_error_message or '上游服务返回错误'}",
                status_code=400,
            )
        return parsed

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

    def _extract_upstream_error_message(self, payload: dict[str, Any]) -> str | None:
        direct_fields = [payload.get("message"), payload.get("detail"), payload.get("error_message")]
        for field in direct_fields:
            if isinstance(field, str) and field.strip():
                return field.strip()
        error_field = payload.get("error")
        if isinstance(error_field, str) and error_field.strip():
            return error_field.strip()
        if isinstance(error_field, dict):
            for key in ("message", "detail", "error", "reason", "type"):
                value = error_field.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        code = payload.get("code")
        if isinstance(code, str) and code.strip():
            return code.strip()
        return None

    def _has_image_candidate(self, payload: dict[str, Any]) -> bool:
        data_items = payload.get("data")
        if isinstance(data_items, list) and data_items:
            first_item = data_items[0]
            if isinstance(first_item, dict):
                for key in ("b64_json", "image_base64", "base64", "url", "image_url", "download_url", "image"):
                    value = first_item.get(key)
                    if isinstance(value, str) and value.strip():
                        return True
            if isinstance(first_item, str) and first_item.strip():
                return True
        for key in ("b64_json", "image_base64", "base64", "url", "image_url", "download_url", "image", "output"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return True
        return False

    def _is_explicit_upstream_error_payload(self, payload: dict[str, Any]) -> bool:
        if isinstance(payload.get("error"), (str, dict)):
            return True
        if payload.get("success") is False:
            return True
        status = payload.get("status")
        if isinstance(status, str) and status.lower() in {"error", "failed", "fail"}:
            return True
        code = payload.get("code")
        if isinstance(code, str):
            lowered_code = code.lower()
            if any(marker in lowered_code for marker in ("error", "fail", "invalid")):
                return True
        if self._has_image_candidate(payload):
            return False
        for key in ("message", "detail", "error_message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return True
        return False

    def _extract_error_message_from_raw_text(self, raw_text: str) -> str | None:
        stripped = (raw_text or "").strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped[:120]
        if not isinstance(parsed, dict):
            return stripped[:120]
        return self._extract_upstream_error_message(parsed)

    def _download_binary(
        self,
        *,
        provider_id: str,
        model_name: str,
        endpoint: str,
    ) -> bytes:
        try:
            with request.urlopen(endpoint, timeout=45) as response:
                return response.read()
        except url_error.HTTPError as error:
            self._log_upstream_failure(
                provider_id=provider_id,
                model_name=model_name,
                endpoint=endpoint,
                http_status=error.code,
                reason="download_http_error",
            )
            raise DomainError(code="E-1004", message=f"图片下载失败：HTTP {error.code}", status_code=400) from error
        except (url_error.URLError, TimeoutError, OSError) as error:
            self._log_upstream_failure(
                provider_id=provider_id,
                model_name=model_name,
                endpoint=endpoint,
                http_status=None,
                reason=f"download_error:{type(error).__name__}",
            )
            raise DomainError(code="E-1004", message="图片下载失败", status_code=400) from error

    def _log_upstream_failure(
        self,
        *,
        provider_id: str,
        model_name: str,
        endpoint: str,
        http_status: int | None,
        reason: str,
    ) -> None:
        logger.warning(
            "生图上游失败 provider_id=%s model_name=%s endpoint=%s http_status=%s reason=%s",
            provider_id,
            model_name,
            endpoint,
            http_status if http_status is not None else "-",
            reason,
        )

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

    def _split_tokens(self, text: str) -> list[str]:
        pieces = re.split(r"[\s,，。！？；、\n\r\t]+", text)
        return [piece.strip() for piece in pieces if piece.strip()]

    def _classify_tokens(self, tokens: list[str]) -> tuple[list[str], list[str]]:
        foods = [token for token in tokens if any(marker in token for marker in FOOD_MARKERS)]
        scenes = [token for token in tokens if any(marker in token for marker in SCENE_MARKERS)]
        return foods, scenes

    def _append_unique(self, target: list[str], values: list[str]) -> None:
        existing = set(target)
        for value in values:
            if value not in existing:
                target.append(value)
                existing.add(value)

    def _select_asset_refs(self, content_mode: str, source_assets: list[dict[str, Any]]) -> list[str]:
        source_ids = [item["asset_id"] for item in source_assets if item.get("asset_id")]
        if not source_ids:
            return []
        if content_mode == "scenic":
            preferred_types = {"scenic_name", "text", "transcript", "image"}
        elif content_mode == "food_scenic":
            preferred_types = {"food_name", "scenic_name", "text", "transcript", "image"}
        else:
            preferred_types = {"food_name", "text", "transcript", "image"}
        selected_ids = [
            item["asset_id"]
            for item in source_assets
            if item.get("asset_id") and item.get("asset_type") in preferred_types
        ]
        return selected_ids or source_ids

    def _build_context_text(
        self,
        content_mode: str,
        foods: list[str],
        scenes: list[str],
        keywords: list[str],
    ) -> str:
        food_text = "、".join(foods[:4]) if foods else "未指定食材"
        scene_text = "、".join(scenes[:4]) if scenes else "未指定场景"
        keyword_text = "、".join(keywords[:6]) if keywords else "无额外关键词"
        if content_mode == "scenic":
            return f"重点呈现场景氛围，核心场景：{scene_text}；辅助关键词：{keyword_text}。"
        if content_mode == "food_scenic":
            return f"平衡食材与场景，食材：{food_text}；场景：{scene_text}；关键词：{keyword_text}。"
        return f"重点呈现食材细节，核心食材：{food_text}；环境点缀：{scene_text}；关键词：{keyword_text}。"

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
            if self._is_demo_provider_base_url(provider.get("base_url")):
                raise CopyModelError("demo_provider_skip", "占位提供商跳过模型文案生成")
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
            payload = self._parse_copy_payload(raw_text)
            return self._normalize_copy_payload(job=job, payload=payload)
        except (CopyModelError, DomainError, ValueError, KeyError, TypeError) as error:
            logger.warning("文案生成降级到模板: job_id=%s reason=%s", job["id"], str(error))
            return self._build_copy_fallback(job=job, style=style, images=images, content_mode=content_mode)

    def _is_demo_provider_base_url(self, base_url: Any) -> bool:
        if not isinstance(base_url, str) or not base_url.strip():
            return False
        try:
            hostname = (urlparse(base_url).hostname or "").lower()
        except Exception:
            return False
        return hostname == "example.com"

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

    def _build_copy_system_prompt(self, content_mode: str) -> str:
        mode_prompt = {
            "food": "突出食材细节、操作步骤和可复刻性。",
            "scenic": "突出场景氛围、动线和镜头叙事。",
            "food_scenic": "兼顾食材表达与场景叙事，强调融合感。",
        }.get(content_mode, "输出高质量可发布图文内容。")
        return (
            "你是 Savory Canvas 资深内容编辑。"
            "请输出严格 JSON，不要输出 Markdown。"
            "JSON 结构必须为："
            '{"title":"", "intro":"", "guide_sections":[{"heading":"","content":""}], "ending":"", "full_text":""}。'
            f"{mode_prompt}"
            "要求：中文表达自然，信息密度高，guide_sections 至少 3 段，每段内容不少于 24 个字。"
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
        prompt_preview = "\n".join(
            f"- 图{item['image_index']}：{item.get('prompt_text', '')[:120]}"
            for item in images[:3]
        )
        return (
            f"风格名称：{style.get('name', '未命名风格')}\n"
            f"风格配置：{self._format_style_payload(style.get('style_payload') or {})}\n"
            f"内容模式：{content_mode}\n"
            f"提取食材：{foods}\n"
            f"提取场景：{scenes}\n"
            f"关键词：{keywords}\n"
            f"图片提示词摘要：\n{prompt_preview}\n"
            f"总图片数量：{len(images)}\n"
            "请生成可直接发布的完整图文文案。"
        )

    def _call_text_model_for_copy(
        self,
        *,
        provider: dict[str, Any],
        model_name: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        protocol_order = self._build_text_protocol_order(provider.get("api_protocol"))
        last_error: CopyModelError | None = None
        for index, protocol in enumerate(protocol_order):
            endpoint, payload = self._build_text_protocol_payload(
                provider=provider,
                model_name=model_name,
                protocol=protocol,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            try:
                response_payload = self._post_text_json(endpoint=endpoint, api_key=provider["api_key"], payload=payload)
                content = self._extract_text_from_text_payload(response_payload, protocol)
                if not content.strip():
                    raise CopyModelError("empty_content", "上游未返回文案内容")
                return content
            except CopyModelError as error:
                last_error = error
                if index == 0 and self._should_retry_text_protocol(error):
                    continue
                break
        raise CopyModelError("protocol_both_failed", last_error.detail if last_error else "双协议调用失败")

    def _build_text_protocol_order(self, configured_protocol: str | None) -> list[str]:
        if configured_protocol == "chat_completions":
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
                    "input": user_prompt,
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

    def _post_text_json(self, *, endpoint: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        upstream_request = request.Request(url=endpoint, method="POST", headers=headers, data=request_body)
        try:
            with request.urlopen(upstream_request, timeout=30) as response:
                raw_text = response.read().decode("utf-8")
        except url_error.HTTPError as error:
            body_text = error.read().decode("utf-8", errors="ignore")
            if error.code in {404, 405}:
                raise CopyModelError("protocol_endpoint_not_supported", f"HTTP {error.code}") from error
            if self._looks_like_text_protocol_incompatible(error.code, body_text):
                raise CopyModelError("protocol_incompatible", f"HTTP {error.code}") from error
            raise CopyModelError("upstream_http_error", f"HTTP {error.code}") from error
        except (url_error.URLError, TimeoutError, OSError) as error:
            raise CopyModelError("upstream_network", str(error)) from error
        try:
            payload_json = json.loads(raw_text)
        except json.JSONDecodeError as error:
            raise CopyModelError("upstream_invalid_json", "上游文案响应不是有效 JSON") from error
        if not isinstance(payload_json, dict):
            raise CopyModelError("upstream_invalid_payload", "上游文案响应结构非法")
        return payload_json

    def _looks_like_text_protocol_incompatible(self, status_code: int, body_text: str) -> bool:
        lowered = body_text.lower()
        common_markers = ["unsupported", "not support", "invalid_request_error", "messages", "input", "instructions"]
        if status_code == 400:
            return any(marker in lowered for marker in common_markers)
        if status_code >= 500:
            server_markers = ["not implemented", "convert_request_failed", "new_api_error"]
            return any(marker in lowered for marker in (common_markers + server_markers))
        return False

    def _should_retry_text_protocol(self, error: CopyModelError) -> bool:
        return error.reason in {"protocol_endpoint_not_supported", "protocol_incompatible"}

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
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            if part["text"].strip():
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
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    if item["text"].strip():
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

    def _build_copy_fallback(
        self,
        *,
        job: dict[str, Any],
        style: dict[str, Any],
        images: list[dict[str, Any]],
        content_mode: str,
    ) -> dict[str, Any]:
        if content_mode == "scenic":
            guide_sections = [
                {"heading": "场景观察", "content": "先明确主场景与叙事焦点，再根据时间与天气设定光线层次，确保画面情绪统一。"},
                {"heading": "构图建议", "content": "建议以前景引导线连接中景主体和背景环境，形成稳定视觉动线并保留空间呼吸感。"},
                {"heading": "发布建议", "content": "发布时补充地点标签、拍摄时间与镜头语言说明，能显著提升内容代入感和收藏率。"},
            ]
            title = f"{style['name']} 场景图文指南"
            intro = f"本次共生成 {len(images)} 张场景配图，可用于氛围化内容发布。"
            ending = "建议先发布场景主图，再追加细节图和拍摄花絮，形成完整叙事闭环。"
        elif content_mode == "food_scenic":
            guide_sections = [
                {"heading": "食材与场景协调", "content": "食材主体建议放在视觉中心，场景元素围绕其展开，突出“可食性 + 氛围感”的双重叙事。"},
                {"heading": "拍摄与出片建议", "content": "近景强调菜品质感，中远景强调场景氛围，注意色温统一，避免主体与环境出现割裂。"},
                {"heading": "发布建议", "content": "文案可采用“场景开场-菜品细节-行动引导”三段结构，能提升停留时长和互动转化。"},
            ]
            title = f"{style['name']} 混合模式图文指南"
            intro = f"本次共生成 {len(images)} 张食材与场景结合配图，可用于综合型内容发布。"
            ending = "建议按平台定位微调语气，并在结尾加入明确行动引导。"
        else:
            guide_sections = [
                {"heading": "准备食材", "content": "建议先按主料、辅料、调味三类整理，并提前完成称量和预处理，确保烹饪节奏稳定。"},
                {"heading": "烹饪步骤", "content": "按“高温定型-中火入味-低温收汁”推进，可同时兼顾口感层次与出品稳定性。"},
                {"heading": "发布建议", "content": "文案中同步写清关键火候、替代食材和失败避坑点，能明显提升内容实用价值。"},
            ]
            title = f"{style['name']} 图文指南"
            intro = f"本次共生成 {len(images)} 张配图，可用于内容发布。"
            ending = "建议发布前补充食材克重和时间节点，增强复刻成功率。"
        full_text = "\n".join([title, intro] + [f"{item['heading']}：{item['content']}" for item in guide_sections] + [ending])
        return {
            "id": new_id(),
            "job_id": job["id"],
            "title": title,
            "intro": intro,
            "guide_sections": guide_sections,
            "ending": ending,
            "full_text": full_text,
            "created_at": now_iso(),
        }

    def _fail(self, job_id: str, error_code: str, error_message: str) -> None:
        self._advance(
            job_id,
            status="failed",
            progress=100,
            stage="finalize",
            stage_message="任务失败",
            error_code=error_code,
            error_message=error_message,
        )

    def _finish(
        self,
        job_id: str,
        *,
        status: str,
        error_code: str | None,
        error_message: str | None,
        stage_message: str,
    ) -> None:
        self._advance(
            job_id,
            status=status,
            progress=100,
            stage="finalize",
            stage_message=stage_message,
            error_code=error_code,
            error_message=error_message,
            log_status="success" if status in {"success", "partial_success"} else status,
        )
