from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
from pathlib import Path
from typing import Any
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
from backend.app.workers.generation.pipeline_mixin import CopyModelError, GenerationPipelineMixin

NON_VISUAL_STYLE_KEYS = {"image_count", "style_prompt", "force_partial_fail", "draft_style_id", "allocation_plan"}
ASSET_EXTRACT_SYSTEM_PROMPT = (
    "你是资产提取助手。请从输入中提取本次创作资产，输出严格 JSON："
    '{"locations":[""],"scenes":[""],"foods":[""],"keywords":[""],"confidence":0.0}。'
    "要求："
    "1) locations 只放地点（城市/区域）；"
    "2) scenes 只放景点地标；"
    "3) foods 只放食物饮品；"
    "4) keywords 仅保留与地点/景点/食物强相关词；"
    "5) 去重并过滤空值；"
    "6) 不要输出风格词和画法词；"
    "7) 只输出 JSON，不要 Markdown，不要解释。"
)
PROMPT_PLAN_SYSTEM_PROMPT = (
    "你是生图提示词规划助手。请根据风格、资产和目标张数输出严格 JSON："
    '{"items":[{"prompt_text":"", "asset_refs":["asset_id"]}]}。'
    "要求："
    "1) items 数量必须等于目标张数；"
    "2) 每个 prompt_text 只能描述一张图，禁止拼贴和多画面合成；"
    "3) 各图主体与叙事重点要有差异，但风格、色彩与质感必须统一；"
    "4) 每条 prompt_text 必须包含主体、场景、构图、镜头、光线、氛围与细节约束；"
    "5) asset_refs 只允许填写输入素材中的 asset_id，且至少 1 个；"
    "6) 只输出 JSON，不要 Markdown，不要解释。"
)
logger = logging.getLogger(__name__)


class GenerationWorker(GenerationPipelineMixin):
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
            planned_images = self._build_planned_copy_images(prompt_specs)
            image_task = asyncio.create_task(
                self._run_image_generate_stage(
                    job=job,
                    prompt_specs=prompt_specs,
                    source_assets=assets,
                    style=style,
                    image_provider=image_provider,
                    image_model_name=image_model_name,
                    allow_image_reference=allow_image_reference,
                )
            )
            copy_task = asyncio.create_task(
                self._run_copy_generate_stage(
                    job=job,
                    style=style,
                    planned_images=planned_images,
                    content_mode=content_mode,
                    breakdown=breakdown,
                )
            )
            image_outcome, copy_outcome = await asyncio.gather(image_task, copy_task, return_exceptions=True)

            if isinstance(image_outcome, Exception):
                if isinstance(image_outcome, DomainError):
                    self._fail(job_id, image_outcome.code, image_outcome.message)
                else:
                    self._fail(job_id, "E-1099", "生成流程异常")
                return
            created_images, failed_images = image_outcome

            copy_error_message: str | None = None
            if isinstance(copy_outcome, Exception):
                logger.exception("文案并行任务异常: job_id=%s", job_id)
                copy_error_message = "文案生成失败：系统内部错误"
            else:
                copy_error_message = copy_outcome

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
        if failed_images > 0:
            stage_message = "图片生成完成，部分图片失败"
        else:
            stage_message = "图片生成完成"
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
            copy_result = self._generate_copy_result(
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
            logger.warning("文案生成失败，保留已产出的图片结果: job_id=%s reason=%s", job["id"], error.message)
            self.result_repo.upsert_copy(
                {
                    "id": new_id(),
                    "job_id": job["id"],
                    "title": "",
                    "intro": "",
                    "guide_sections": [],
                    "ending": "",
                    "full_text": "",
                    "created_at": now_iso(),
                }
            )
            self._advance(
                job["id"],
                "running",
                92,
                "copy_generate",
                "文案生成失败，已保留图片结果",
                error_code=error.code,
                error_message=error.message,
                log_status="failed",
            )
            return error.message

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
        extracted = self._resolve_asset_extraction(
            session_id=job["session_id"],
            source_assets=source_assets,
            content_mode=content_mode,
        )

        return {
            "job_id": job["id"],
            "session_id": job["session_id"],
            "content_mode": content_mode,
            "source_assets": source_assets,
            "extracted": extracted,
            "created_at": now_iso(),
        }

    def _resolve_asset_extraction(
        self,
        *,
        session_id: str,
        source_assets: list[dict[str, Any]],
        content_mode: str,
    ) -> dict[str, list[str]]:
        state = self.inspiration_repo.get_state(session_id) or {}
        candidates = state.get("asset_candidates") if isinstance(state.get("asset_candidates"), dict) else {}
        candidate_foods = self._normalize_asset_values(candidates.get("foods") if isinstance(candidates, dict) else [])
        candidate_scenes = self._normalize_asset_values(candidates.get("scenes") if isinstance(candidates, dict) else [])
        candidate_keywords = self._normalize_asset_values(candidates.get("keywords") if isinstance(candidates, dict) else [])
        if candidate_foods or candidate_scenes or candidate_keywords:
            return {
                "foods": candidate_foods[:10],
                "scenes": candidate_scenes[:10],
                "keywords": candidate_keywords[:15],
            }
        return self._extract_assets_with_llm(
            session_id=session_id,
            source_assets=source_assets,
            content_mode=content_mode,
        )

    def _extract_assets_with_llm(
        self,
        *,
        session_id: str,
        source_assets: list[dict[str, Any]],
        content_mode: str,
    ) -> dict[str, list[str]]:
        context_text = self._build_asset_extract_user_prompt(source_assets=source_assets, content_mode=content_mode)
        provider, model_name = self._resolve_text_model_provider()
        try:
            raw_text = self._call_text_model_for_copy(
                provider=provider,
                model_name=model_name,
                system_prompt=ASSET_EXTRACT_SYSTEM_PROMPT,
                user_prompt=context_text,
            )
            payload = self._parse_asset_extract_payload(raw_text)
        except CopyModelError as error:
            raise DomainError(
                code="E-1004",
                message=f"资产提取失败：{error.detail}",
                status_code=503,
            ) from error
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise DomainError(
                code="E-1004",
                message="资产提取失败：模型返回格式异常，请重试",
                status_code=503,
            ) from error

        locations = self._normalize_asset_values(payload.get("locations"))
        scenes = self._merge_unique_values(self._normalize_asset_values(payload.get("scenes")), locations)
        foods = self._normalize_asset_values(payload.get("foods"))
        keywords = self._merge_unique_values(
            self._normalize_asset_values(payload.get("keywords")),
            locations + scenes + foods,
        )
        if not foods and content_mode in {"food", "food_scenic"}:
            raise DomainError(code="E-1004", message="资产提取失败：缺少可用食物信息，请补充需求后重试", status_code=400)
        if not scenes and content_mode in {"scenic", "food_scenic"}:
            raise DomainError(code="E-1004", message="资产提取失败：缺少可用景点信息，请补充需求后重试", status_code=400)
        if not keywords:
            keywords = self._merge_unique_values([], foods + scenes)
        return {
            "foods": foods[:10],
            "scenes": scenes[:10],
            "keywords": keywords[:15],
        }

    def _build_asset_extract_user_prompt(
        self,
        *,
        source_assets: list[dict[str, Any]],
        content_mode: str,
    ) -> str:
        lines = [f"内容模式：{content_mode}", "素材输入："]
        for asset in source_assets:
            asset_type = str(asset.get("asset_type") or "").strip()
            content = str(asset.get("content") or "").strip()
            if not asset_type or not content:
                continue
            lines.append(f"- {asset_type}: {content}")
        if len(lines) <= 2:
            raise DomainError(code="E-1003", message="素材不足，无法提取资产", status_code=400)
        lines.append("请严格输出 JSON，不要解释。")
        return "\n".join(lines)

    def _parse_asset_extract_payload(self, model_text: str) -> dict[str, Any]:
        text = model_text.strip()
        if text.startswith("```"):
            lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("资产提取响应缺少 JSON 对象")
        payload = json.loads(text[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("资产提取响应结构非法")
        return payload

    def _normalize_asset_values(self, values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            if len(text) < 2 or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized

    def _merge_unique_values(self, base_values: list[str], extra_values: list[str]) -> list[str]:
        merged = list(base_values)
        seen = set(base_values)
        for value in extra_values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
        return merged

    def _build_prompt_specs(
        self,
        image_count: int,
        breakdown: dict[str, Any],
        style: dict[str, Any],
        content_mode: str,
    ) -> list[dict[str, Any]]:
        source_assets = breakdown.get("source_assets") or []
        available_asset_ids = [
            str(item.get("asset_id")).strip()
            for item in source_assets
            if str(item.get("asset_id") or "").strip()
        ]
        if not available_asset_ids:
            raise DomainError(code="E-1003", message="素材不足，无法生成提示词", status_code=400)
        style_payload = style.get("style_payload") or {}
        allocation_specs = self._build_prompt_specs_from_allocation(
            style_payload=style_payload,
            image_count=image_count,
            available_asset_ids=available_asset_ids,
        )
        if allocation_specs is not None:
            return allocation_specs
        style_description = self._format_style_payload(style.get("style_payload") or {})
        provider, model_name = self._resolve_text_model_provider()
        user_prompt = self._build_prompt_plan_user_prompt(
            image_count=image_count,
            style_description=style_description,
            breakdown=breakdown,
            source_assets=source_assets,
            content_mode=content_mode,
        )
        try:
            raw_text = self._call_text_model_for_copy(
                provider=provider,
                model_name=model_name,
                system_prompt=PROMPT_PLAN_SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )
            payload = self._parse_prompt_plan_payload(raw_text)
        except CopyModelError as error:
            raise DomainError(code="E-1004", message=f"提示词生成失败：{error.detail}", status_code=503) from error
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise DomainError(code="E-1004", message="提示词生成失败：模型返回格式异常，请重试", status_code=503) from error
        return self._normalize_prompt_plan_items(
            payload=payload,
            image_count=image_count,
            available_asset_ids=available_asset_ids,
        )

    def _build_prompt_specs_from_allocation(
        self,
        *,
        style_payload: dict[str, Any],
        image_count: int,
        available_asset_ids: list[str],
    ) -> list[dict[str, Any]] | None:
        raw_plan = style_payload.get("allocation_plan")
        if raw_plan is None:
            return None
        if not isinstance(raw_plan, list) or len(raw_plan) < image_count:
            raise DomainError(code="E-1004", message="分图方案缺失或不完整，请返回灵感对话重新确认", status_code=400)
        valid_asset_ids = set(available_asset_ids)
        prompt_specs: list[dict[str, Any]] = []
        for index, raw_item in enumerate(raw_plan[:image_count], start=1):
            if not isinstance(raw_item, dict):
                raise DomainError(code="E-1004", message="分图方案格式异常，请返回灵感对话重新确认", status_code=400)
            if not bool(raw_item.get("confirmed")):
                raise DomainError(code="E-1004", message="分图方案尚未确认，请先在灵感对话中确认后再生成", status_code=400)
            focus_description = str(raw_item.get("focus_description") or "").strip()
            if not focus_description:
                raise DomainError(code="E-1004", message="分图方案缺少重点内容，请返回灵感对话重新确认", status_code=400)
            source_asset_ids = raw_item.get("source_asset_ids")
            if not isinstance(source_asset_ids, list):
                source_asset_ids = []
            asset_refs: list[str] = []
            seen: set[str] = set()
            for value in source_asset_ids:
                asset_id = str(value).strip()
                if not asset_id or asset_id in seen or asset_id not in valid_asset_ids:
                    continue
                seen.add(asset_id)
                asset_refs.append(asset_id)
            if not asset_refs:
                raise DomainError(code="E-1004", message="分图方案缺少可追溯素材来源，请返回灵感对话重新确认", status_code=400)
            prompt_text = self._build_prompt_text_from_allocation(raw_item, index=index)
            prompt_specs.append(
                {
                    "prompt_text": self._ensure_single_image_prompt(prompt_text),
                    "asset_refs": asset_refs,
                }
            )
        return prompt_specs

    def _build_prompt_text_from_allocation(self, plan_item: dict[str, Any], *, index: int) -> str:
        focus_title = str(plan_item.get("focus_title") or f"第{index}张重点").strip()
        focus_description = str(plan_item.get("focus_description") or "").strip()
        locations = "、".join(str(value).strip() for value in (plan_item.get("locations") or []) if str(value).strip()) or "无"
        scenes = "、".join(str(value).strip() for value in (plan_item.get("scenes") or []) if str(value).strip()) or "无"
        foods = "、".join(str(value).strip() for value in (plan_item.get("foods") or []) if str(value).strip()) or "无"
        keywords = "、".join(str(value).strip() for value in (plan_item.get("keywords") or []) if str(value).strip()) or "无"
        return (
            f"{focus_title}：{focus_description}\n"
            f"地点：{locations}；景点：{scenes}；美食：{foods}；关键词：{keywords}。\n"
            "强约束：只能围绕本条列出的地点/景点/美食展开，禁止引入未确认的实体。"
        )

    def _build_prompt_plan_user_prompt(
        self,
        *,
        image_count: int,
        style_description: str,
        breakdown: dict[str, Any],
        source_assets: list[dict[str, Any]],
        content_mode: str,
    ) -> str:
        extracted = breakdown.get("extracted") or {}
        foods = "、".join(extracted.get("foods") or []) or "无"
        scenes = "、".join(extracted.get("scenes") or []) or "无"
        keywords = "、".join(extracted.get("keywords") or []) or "无"
        lines = [
            f"内容模式：{content_mode}",
            f"目标张数：{image_count}",
            f"风格描述：{style_description}",
            f"资产提取-食物：{foods}",
            f"资产提取-景点：{scenes}",
            f"资产提取-关键词：{keywords}",
            "可用素材列表：",
        ]
        for source in source_assets:
            asset_id = str(source.get("asset_id") or "").strip()
            asset_type = str(source.get("asset_type") or "").strip()
            content = str(source.get("content") or "").strip()
            if not asset_id:
                continue
            lines.append(f"- asset_id={asset_id}; asset_type={asset_type}; content={content}")
        lines.append("请输出 JSON。")
        return "\n".join(lines)

    def _parse_prompt_plan_payload(self, model_text: str) -> dict[str, Any]:
        text = model_text.strip()
        if text.startswith("```"):
            lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("提示词规划响应缺少 JSON 对象")
        payload = json.loads(text[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("提示词规划响应结构非法")
        return payload

    def _normalize_prompt_plan_items(
        self,
        *,
        payload: dict[str, Any],
        image_count: int,
        available_asset_ids: list[str],
    ) -> list[dict[str, Any]]:
        items = payload.get("items")
        if not isinstance(items, list) or len(items) < image_count:
            raise ValueError("提示词规划数量不足")
        valid_asset_ids = set(available_asset_ids)
        normalized_specs: list[dict[str, Any]] = []
        for item in items[:image_count]:
            if not isinstance(item, dict):
                raise ValueError("提示词规划项结构非法")
            prompt_text = str(item.get("prompt_text") or "").strip()
            if not prompt_text:
                raise ValueError("提示词规划缺少 prompt_text")
            raw_asset_refs = item.get("asset_refs") if isinstance(item.get("asset_refs"), list) else []
            asset_refs: list[str] = []
            seen: set[str] = set()
            for value in raw_asset_refs:
                asset_id = str(value).strip()
                if not asset_id or asset_id not in valid_asset_ids or asset_id in seen:
                    continue
                seen.add(asset_id)
                asset_refs.append(asset_id)
            if not asset_refs:
                raise ValueError("提示词规划缺少有效 asset_refs")
            normalized_specs.append(
                {
                    "prompt_text": self._ensure_single_image_prompt(prompt_text),
                    "asset_refs": asset_refs,
                }
            )
        return normalized_specs

    def _ensure_single_image_prompt(self, prompt_text: str) -> str:
        normalized = prompt_text.strip()
        if "请只生成一张图片" not in normalized:
            normalized = f"请只生成一张图片。\n{normalized}"
        if "禁止拼贴" not in normalized:
            normalized = (
                f"{normalized}\n"
                "强约束：禁止拼贴、禁止九宫格、禁止分镜、禁止多画面合成、禁止任何文字水印。"
            )
        if "仅借鉴风格" not in normalized:
            normalized = (
                f"{normalized}\n"
                "参考图约束：若提供参考图，仅借鉴笔触、配色与版式，不得复制参考图中的具体地点、食物、人物或文字。"
            )
        return normalized

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
                if last_error_message is None:
                    last_error_message = error.message
                else:
                    current_is_network_error = "网络异常" in error.message
                    previous_is_network_error = "网络异常" in last_error_message
                    if previous_is_network_error and not current_is_network_error:
                        last_error_message = error.message
                    elif previous_is_network_error == current_is_network_error:
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

