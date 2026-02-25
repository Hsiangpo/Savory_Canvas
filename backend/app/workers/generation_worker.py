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
            copy_error_message: str | None = None
            self._advance(job_id, "running", 82, "copy_generate", "正在生成文案结构")
            try:
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
            except DomainError as error:
                copy_error_message = error.message
                logger.warning("文案生成失败，保留已产出的图片结果: job_id=%s reason=%s", job_id, error.message)
                self.result_repo.upsert_copy(
                    {
                        "id": new_id(),
                        "job_id": job_id,
                        "title": "",
                        "intro": "",
                        "guide_sections": [],
                        "ending": "",
                        "full_text": "",
                        "created_at": now_iso(),
                    }
                )
                self._advance(
                    job_id,
                    "running",
                    92,
                    "copy_generate",
                    "文案生成失败，已保留图片结果",
                    error_code=error.code,
                    error_message=error.message,
                    log_status="failed",
                )

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
        source_assets_by_id = {asset.get("id"): asset for asset in source_assets if isinstance(asset.get("id"), str)}
        tagged_reference_asset_ids = self._collect_tagged_reference_asset_ids(session_id)
        sample_asset_id = style_payload.get("sample_image_asset_id")
        if isinstance(sample_asset_id, str) and sample_asset_id.strip():
            sample_asset = self.asset_repo.get(sample_asset_id.strip())
            sample_path = sample_asset.get("file_path") if sample_asset else None
            if isinstance(sample_path, str) and sample_path.strip():
                references.append(sample_path.strip())
        sample_file_path = style_payload.get("sample_image_file_path")
        if isinstance(sample_file_path, str) and sample_file_path.strip():
            references.append(sample_file_path.strip())
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
        headers = self._build_upstream_headers(api_key)
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
        if self._looks_like_html_error_page(stripped):
            return "上游网关拒绝访问（返回 HTML 页面）"
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped[:120]
        if not isinstance(parsed, dict):
            return stripped[:120]
        return self._extract_upstream_error_message(parsed)

    def _build_upstream_headers(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        }

    def _looks_like_html_error_page(self, body_text: str) -> bool:
        lowered = (body_text or "").strip().lower()
        return lowered.startswith("<!doctype html") or lowered.startswith("<html")

    def _build_upstream_http_error_detail(self, *, status_code: int, body_text: str) -> str:
        if self._looks_like_html_error_page(body_text):
            return f"HTTP {status_code}: 上游网关拒绝访问（返回 HTML 页面）"
        return f"HTTP {status_code}: {body_text[:120]}"

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
            try:
                payload = self._parse_copy_payload(raw_text)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                retry_text = self._call_text_model_for_copy(
                    provider=provider,
                    model_name=model_name,
                    system_prompt=self._build_copy_system_prompt(content_mode, strict_json=True),
                    user_prompt=(
                        f"{user_prompt}\n"
                        "上一次输出未满足 JSON 结构，请仅输出一个合法 JSON 对象，不要 Markdown、不要解释。"
                    ),
                )
                payload = self._parse_copy_payload(retry_text)
            return self._normalize_copy_payload(job=job, payload=payload)
        except CopyModelError as error:
            raise DomainError(code="E-1004", message=f"文案生成失败：{error.detail}", status_code=503) from error
        except DomainError:
            raise
        except (ValueError, KeyError, TypeError) as error:
            raise DomainError(code="E-1004", message="文案生成失败：模型返回格式异常，请重试", status_code=503) from error

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

    def _build_copy_system_prompt(self, content_mode: str, strict_json: bool = False) -> str:
        mode_prompt = {
            "food": "突出食材细节、操作步骤和可复刻性。",
            "scenic": "突出场景氛围、动线和镜头叙事。",
            "food_scenic": "兼顾食材表达与场景叙事，强调融合感。",
        }.get(content_mode, "输出高质量可发布图文内容。")
        return (
            "你是 Savory Canvas 资深内容总编。"
            "请输出严格 JSON，不要输出 Markdown。"
            "JSON 结构必须为："
            '{"title":"", "intro":"", "guide_sections":[{"heading":"","content":""}], "ending":"", "full_text":""}。'
            f"{mode_prompt}"
            "要求：中文表达自然、专业、可发布，避免口号化空话。"
            "标题要具体有吸引力，导语要给出清晰价值承诺。"
            "guide_sections 至少 3 段，每段需可执行，建议包含“看点/路线/实操建议/避坑提示”等。"
            "ending 需包含行动引导（收藏、评论、到店/到景点打卡等）。"
            "full_text 必须是可直接发布的完整长文，不是字段拼接。"
            + ("本次必须只输出一个 JSON 对象，不允许任何额外文本。" if strict_json else "")
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
        provider_id = str(provider.get("id") or "").strip()
        max_attempts = 3
        last_error: CopyModelError | None = None

        for attempt in range(1, max_attempts + 1):
            protocol_order = self._build_text_protocol_order(provider_id, provider.get("api_protocol"))
            for index, protocol in enumerate(protocol_order):
                endpoint, payload = self._build_text_protocol_payload(
                    provider=provider,
                    model_name=model_name,
                    protocol=protocol,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
                try:
                    response_payload = self._post_text_json(
                        endpoint=endpoint,
                        api_key=provider["api_key"],
                        payload=payload,
                        provider_id=provider_id,
                        model_name=model_name,
                    )
                    content = self._extract_text_from_text_payload(response_payload, protocol)
                    if not content.strip():
                        raise CopyModelError("empty_content", "上游未返回文案内容")
                    if provider_id:
                        self._text_protocol_overrides[provider_id] = protocol
                    return content
                except CopyModelError as error:
                    last_error = error
                    if index == 0 and self._should_retry_text_protocol(error):
                        logger.warning(
                            "文案协议回退重试: from=%s to=%s provider=%s model=%s reason=%s detail=%s",
                            protocol,
                            protocol_order[1],
                            provider_id or "-",
                            model_name,
                            error.reason,
                            error.detail,
                        )
                        continue
                    break
            if last_error and attempt < max_attempts and self._should_retry_text_request(last_error):
                logger.warning(
                    "文案模型重试: attempt=%s/%s provider=%s model=%s reason=%s detail=%s",
                    attempt,
                    max_attempts,
                    provider_id or "-",
                    model_name,
                    last_error.reason,
                    last_error.detail,
                )
                continue
            break

        raise CopyModelError("protocol_both_failed", last_error.detail if last_error else "双协议调用失败")

    def _build_text_protocol_order(self, provider_id: str, configured_protocol: str | None) -> list[str]:
        override_protocol = self._text_protocol_overrides.get(provider_id)
        active_protocol = override_protocol or configured_protocol
        if active_protocol == "chat_completions":
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
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": user_prompt,
                                }
                            ],
                        }
                    ],
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

    def _post_text_json(
        self,
        *,
        endpoint: str,
        api_key: str,
        payload: dict[str, Any],
        provider_id: str,
        model_name: str,
    ) -> dict[str, Any]:
        headers = self._build_upstream_headers(api_key)
        request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        upstream_request = request.Request(url=endpoint, method="POST", headers=headers, data=request_body)
        try:
            with request.urlopen(upstream_request, timeout=45) as response:
                raw_text = response.read().decode("utf-8")
        except url_error.HTTPError as error:
            body_text = error.read().decode("utf-8", errors="ignore")
            if error.code in {404, 405}:
                raise CopyModelError("protocol_endpoint_not_supported", f"HTTP {error.code}") from error
            if self._looks_like_text_protocol_incompatible(error.code, body_text):
                raise CopyModelError("protocol_incompatible", f"HTTP {error.code}") from error
            reason = "upstream_retryable_http" if error.code in {408, 409, 425, 429, 500, 502, 503, 504} else "upstream_http_error"
            logger.warning(
                "文案上游 HTTP 失败: provider_id=%s model_name=%s endpoint=%s status=%s",
                provider_id or "-",
                model_name,
                endpoint,
                error.code,
            )
            detail = self._build_upstream_http_error_detail(status_code=error.code, body_text=body_text)
            raise CopyModelError(reason, detail) from error
        except (url_error.URLError, TimeoutError, OSError) as error:
            logger.warning(
                "文案上游网络失败: provider_id=%s model_name=%s endpoint=%s reason=%s",
                provider_id or "-",
                model_name,
                endpoint,
                type(error).__name__,
            )
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
        if status_code == 403 and self._looks_like_html_error_page(body_text):
            return True
        if status_code >= 500:
            server_markers = ["not implemented", "convert_request_failed", "new_api_error"]
            return any(marker in lowered for marker in (common_markers + server_markers))
        return False

    def _should_retry_text_protocol(self, error: CopyModelError) -> bool:
        return error.reason in {"protocol_endpoint_not_supported", "protocol_incompatible"}

    def _should_retry_text_request(self, error: CopyModelError) -> bool:
        return error.reason in {"upstream_network", "upstream_retryable_http"}

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
