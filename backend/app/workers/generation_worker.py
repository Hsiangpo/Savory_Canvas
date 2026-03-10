from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from backend.app.core.errors import DomainError
from backend.app.core.utils import new_id, now_iso
from backend.app.infra import http_client as http_client_module
from backend.app.infra.storage import Storage
from backend.app.repositories.asset_repo import AssetRepository
from backend.app.repositories.inspiration_repo import InspirationRepository
from backend.app.repositories.job_repo import JobRepository
from backend.app.repositories.result_repo import ResultRepository
from backend.app.repositories.session_repo import SessionRepository
from backend.app.repositories.style_repo import StyleRepository
from backend.app.services.model_service import ModelService
from backend.app.workers.generation.asset_breakdown_mixin import GenerationAssetBreakdownMixin
from backend.app.workers.generation.image_gen_mixin import GenerationImageGenMixin
from backend.app.workers.generation.pipeline_mixin import GenerationPipelineMixin
from backend.app.workers.generation.prompt_plan_mixin import GenerationPromptPlanMixin

logger = logging.getLogger(__name__)
request = http_client_module.request


class GenerationWorker(
    GenerationImageGenMixin,
    GenerationPromptPlanMixin,
    GenerationAssetBreakdownMixin,
    GenerationPipelineMixin,
):
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
        self._text_protocol_overrides: dict[str, str] = {}
        self._logger = logger

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
            allow_image_reference = self._supports_image_reference(
                image_model_name=image_model_name,
                capabilities=image_model_capabilities,
            )
            image_outcome = await self._run_image_generate_stage(
                job=job,
                prompt_specs=prompt_specs,
                source_assets=assets,
                style=style,
                image_provider=image_provider,
                image_model_name=image_model_name,
                allow_image_reference=allow_image_reference,
            )
            created_images, failed_images = image_outcome

            copy_error_message: str | None
            try:
                copy_error_message = await self._run_copy_generate_stage(
                    job=job,
                    style=style,
                    planned_images=created_images or self._build_planned_copy_images(prompt_specs),
                    content_mode=content_mode,
                    breakdown=breakdown,
                )
            except Exception:
                logger.exception("文案任务异常: job_id=%s", job_id)
                copy_error_message = "文案生成失败：系统内部错误"

            if self._is_canceled(job_id):
                return
            self._advance(job_id, "running", 95, "finalize", "正在整理结果")
            if failed_images > 0 or copy_error_message:
                partial_error_message = copy_error_message or "部分图片生成失败"
                self._finish(
                    job_id,
                    status="partial_success",
                    error_code="E-1004",
                    error_message=partial_error_message,
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

    def _build_planned_copy_images(self, prompt_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        planned_images: list[dict[str, Any]] = []
        for index, spec in enumerate(prompt_specs, start=1):
            planned_images.append(
                {
                    "image_index": index,
                    "prompt_text": str(spec.get("prompt_text") or "").strip(),
                    "asset_refs": list(spec.get("asset_refs") or []),
                }
            )
        return planned_images

    async def _run_image_generate_stage(
        self,
        *,
        job: dict[str, Any],
        prompt_specs: list[dict[str, Any]],
        source_assets: list[dict[str, Any]],
        style: dict[str, Any],
        image_provider: dict[str, Any],
        image_model_name: str,
        allow_image_reference: bool,
    ) -> tuple[list[dict[str, Any]], int]:
        self._advance(job["id"], "running", 60, "image_generate", "正在生成图片")
        created_images, failed_images, last_error_message = await self._generate_images(
            job=job,
            prompt_specs=prompt_specs,
            source_assets=source_assets,
            style=style,
            image_provider=image_provider,
            image_model_name=image_model_name,
            allow_image_reference=allow_image_reference,
        )
        if not created_images:
            raise DomainError(code="E-1004", message=last_error_message or "图片生成失败", status_code=400)
        stage_message = "图片生成完成，部分图片失败" if failed_images > 0 else "图片生成完成"
        self._complete_stage(job["id"], progress=86, stage="image_generate", stage_message=stage_message)
        return created_images, failed_images

    async def _run_copy_generate_stage(
        self,
        *,
        job: dict[str, Any],
        style: dict[str, Any],
        planned_images: list[dict[str, Any]],
        content_mode: str,
        breakdown: dict[str, Any],
    ) -> str | None:
        self._advance(job["id"], "running", 62, "copy_generate", "正在生成文案结构")
        await asyncio.sleep(0)
        try:
            copy_result = await asyncio.to_thread(
                self._generate_copy_result,
                job=job,
                style=style,
                images=planned_images,
                content_mode=content_mode,
                breakdown=breakdown,
            )
            self._advance(job["id"], "running", 88, "copy_generate", "正在润色文案表达")
            self.result_repo.upsert_copy(copy_result)
            self._complete_stage(job["id"], progress=92, stage="copy_generate", stage_message="文案生成完成")
            return None
        except DomainError as error:
            logger.warning("文案生成失败，切换到本地兜底文案: job_id=%s reason=%s", job["id"], error.message)
            fallback_copy = self._build_fallback_copy_payload(
                job=job,
                style=style,
                images=planned_images,
                content_mode=content_mode,
                breakdown=breakdown,
                error_message=error.message,
            )
            self._advance(job["id"], "running", 88, "copy_generate", "上游文案失败，正在生成兜底文案")
            self.result_repo.upsert_copy(fallback_copy)
            self._complete_stage(job["id"], progress=92, stage="copy_generate", stage_message="文案兜底生成完成")
            return None

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

    def _fail(self, job_id: str, error_code: str, error_message: str) -> None:
        current_job = self.job_repo.get(job_id)
        if current_job:
            current_stage = str(current_job.get("current_stage") or "").strip()
            if current_stage and current_stage != "finalize":
                self._advance(
                    job_id=job_id,
                    status="running",
                    progress=int(current_job.get("progress_percent") or 0),
                    stage=current_stage,
                    stage_message=current_job.get("stage_message") or "阶段失败",
                    error_code=error_code,
                    error_message=error_message,
                    log_status="failed",
                )
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
