from __future__ import annotations

import json
from typing import Any

from backend.app.core.errors import DomainError
from backend.app.core.utils import now_iso

ASSET_EXTRACT_SYSTEM_PROMPT = (
    "你是资产提取助手。请从输入中提取本次创作资产，输出严格 JSON："
    '{"locations":[""],"scenes":[""],"foods":[""],"keywords":[""],"confidence":0.0}。'
    "要求："
    "1) locations 只放地点（城市/区域/地理带），例如西安、陕西、河西走廊；"
    "2) scenes 只放景点地标；"
    "3) foods 只放食物饮品；"
    "4) keywords 仅保留与地点/景点/食物强相关词；"
    "5) 去重并过滤空值；"
    "6) 不要输出风格词和画法词；"
    "7) 像“街边老店、夜市摊位、餐馆内景”这种不属于地点，必须放到 scenes 或 keywords，不得放入 locations；"
    "8) 只输出 JSON，不要 Markdown，不要解释。"
)


class GenerationAssetBreakdownMixin:
    def _build_asset_breakdown(
        self,
        job: dict[str, Any],
        assets: list[dict[str, Any]],
        content_mode: str,
    ) -> dict[str, Any]:
        source_assets = [
            {"asset_id": asset["id"], "asset_type": asset["asset_type"], "content": asset.get("content")}
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
            return {"foods": candidate_foods[:10], "scenes": candidate_scenes[:10], "keywords": candidate_keywords[:15]}
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
        except Exception as error:
            if isinstance(error, DomainError):
                raise
            raise DomainError(code="E-1004", message="资产提取失败：模型返回格式异常，请重试", status_code=503) from error

        locations = self._normalize_asset_values(payload.get("locations"))
        scenes = self._normalize_asset_values(payload.get("scenes"))
        foods = self._normalize_asset_values(payload.get("foods"))
        keywords = self._normalize_asset_values(payload.get("keywords"))
        if not foods and content_mode in {"food", "food_scenic"}:
            raise DomainError(code="E-1004", message="资产提取失败：缺少可用食物信息，请补充需求后重试", status_code=400)
        if not scenes and content_mode in {"scenic", "food_scenic"}:
            raise DomainError(code="E-1004", message="资产提取失败：缺少可用景点信息，请补充需求后重试", status_code=400)
        return {"foods": foods[:10], "scenes": scenes[:10], "keywords": keywords[:15]}

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
