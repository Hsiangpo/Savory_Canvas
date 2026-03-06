from __future__ import annotations

import json
from typing import Any

from backend.app.core.errors import DomainError

PROMPT_PLAN_SYSTEM_PROMPT = (
    "你是生图提示词规划助手。请根据风格、资产和目标张数输出严格 JSON："
    '{"items":[{"prompt_text":"", "asset_refs":["asset_id"]}]}。'
    "要求："
    "1) items 数量必须等于目标张数；"
    "2) 每个 prompt_text 只能描述一张图，禁止拼贴和多画面合成；"
    "3) 各图主体与叙事重点要有差异，但风格、色彩与质感必须统一；"
    "4) 每条 prompt_text 必须是“旅游攻略图解/手账信息图”，不是纯作画插图；"
    "5) 每条 prompt_text 必须包含主体、场景、构图、镜头、光线、氛围与细节约束；"
    "6) 每个景点/美食都要写明“名称+10-20字简短介绍”的标注要求，且禁止出现“（15字）/15字/字数说明”等字样；"
    "7) asset_refs 只允许填写输入素材中的 asset_id，且至少 1 个；"
    "8) 只输出 JSON，不要 Markdown，不要解释。"
)


class GenerationPromptPlanMixin:
    def _build_prompt_specs(
        self,
        image_count: int,
        breakdown: dict[str, Any],
        style: dict[str, Any],
        content_mode: str,
    ) -> list[dict[str, Any]]:
        source_assets = breakdown.get("source_assets") or []
        available_asset_ids = [str(item.get("asset_id")).strip() for item in source_assets if str(item.get("asset_id") or "").strip()]
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
        except Exception as error:
            if isinstance(error, DomainError):
                raise
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
            prompt_specs.append({"prompt_text": self._ensure_single_image_prompt(prompt_text), "asset_refs": asset_refs})
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
            "强约束：只能围绕本条列出的地点/景点/美食展开，禁止引入未确认的实体。\n"
            "画面必须是旅游攻略图解/手账信息图，禁止做成纯风景或纯食物插画。\n"
            "每个景点或美食旁边都要放“名称+10-20字简短介绍”标签，并用箭头或编号连接，且禁止出现“（15字）/15字/字数说明”等字样。\n"
            "构图约束：画面内容必须铺满整张画布，禁止外圈留白、相纸边、画框边、底板边。"
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
            normalized_specs.append({"prompt_text": self._ensure_single_image_prompt(prompt_text), "asset_refs": asset_refs})
        return normalized_specs

    def _ensure_single_image_prompt(self, prompt_text: str) -> str:
        normalized = prompt_text.strip()
        if "请只生成一张图片" not in normalized:
            normalized = f"请只生成一张图片。\n{normalized}"
        if "图解" not in normalized and "信息图" not in normalized:
            normalized = f"{normalized}\n版式约束：输出旅游攻略图解/手账信息图，包含标题区、图标/贴纸、导览箭头和信息标签。"
        if "10-20字简短介绍" not in normalized:
            normalized = f"{normalized}\n标注约束：每个景点/美食都必须配“名称+10-20字简短介绍”的中文标签，且标签正文禁止出现“（15字）/15字/字数说明”。"
        if "禁止拼贴" not in normalized:
            normalized = f"{normalized}\n强约束：禁止拼贴、禁止九宫格、禁止分镜、禁止多画面合成、禁止任何文字水印。"
        if "禁止外圈留白" not in normalized and "禁止留白边框" not in normalized:
            normalized = f"{normalized}\n构图约束：画面需铺满画布，禁止外圈留白、白边、相纸边、画框边与背景底板边。"
        if "仅借鉴风格" not in normalized:
            normalized = f"{normalized}\n参考图约束：若提供参考图，仅借鉴笔触、配色与版式，不得复制参考图中的具体地点、食物、人物或文字。"
        return normalized
