from __future__ import annotations

import json
from typing import Any

from backend.app.core.errors import DomainError
from backend.app.services.inspiration.constants import ALLOCATION_PLAN_SYSTEM_PROMPT, ASSET_CONFIRM_HINT


class InspirationAllocationBuilderMixin:
    def _build_allocation_plan(
        self,
        *,
        session: dict[str, Any],
        state: dict[str, Any],
        user_hint: str,
    ) -> list[dict[str, Any]]:
        image_count_raw = state.get("image_count")
        image_count = int(image_count_raw) if isinstance(image_count_raw, int) else 0
        if image_count <= 0:
            raise DomainError(code="E-1004", message="请先确认生成张数后再分配每张图重点内容", status_code=400)
        assets = self.asset_repo.list_by_session(session["id"])
        source_asset_ids = [str(asset.get("id")).strip() for asset in assets if str(asset.get("id") or "").strip()]
        if not source_asset_ids:
            raise DomainError(code="E-1003", message="素材不足，无法完成分图确认", status_code=400)
        candidates = state.get("asset_candidates") if isinstance(state.get("asset_candidates"), dict) else {}
        locations = "、".join(candidates.get("locations") or []) or "无"
        foods = "、".join(candidates.get("foods") or []) or "无"
        scenes = "、".join(candidates.get("scenes") or []) or "无"
        keywords = "、".join(candidates.get("keywords") or []) or "无"
        style_text = self._format_style_payload_text(self._build_style_payload(state))
        recent_context = self._collect_recent_user_context(session["id"], limit=8) or "无"
        user_prompt = (
            f"目标张数：{image_count}\n"
            f"风格参数：{style_text}\n"
            f"已提取地点：{locations}\n"
            f"已提取美食：{foods}\n"
            f"已提取景点：{scenes}\n"
            f"已提取关键词：{keywords}\n"
            f"最近用户上下文：{recent_context}\n"
            f"本轮补充：{user_hint or '无'}\n"
            f"可用 source_asset_ids：{', '.join(source_asset_ids)}\n"
            "请输出严格 JSON。"
        )
        session_image_urls = self._collect_session_image_asset_urls(session["id"])
        if session_image_urls:
            model_text = self._call_vision_model_with_retry(
                session_id=session["id"],
                system_prompt=ALLOCATION_PLAN_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                image_urls=session_image_urls,
                strict_json=True,
            )
        else:
            model_text = self._call_text_model_with_retry(
                session_id=session["id"],
                system_prompt=ALLOCATION_PLAN_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                strict_json=True,
            )
        return self._parse_allocation_plan_payload(
            model_text=model_text,
            image_count=image_count,
            source_asset_ids=source_asset_ids,
            asset_candidates=candidates,
        )

    def _parse_allocation_plan_payload(
        self,
        *,
        model_text: str,
        image_count: int,
        source_asset_ids: list[str],
        asset_candidates: dict[str, Any],
    ) -> list[dict[str, Any]]:
        text = model_text.strip()
        if text.startswith("```"):
            lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()
        start_index = text.find("{")
        end_index = text.rfind("}")
        if start_index == -1 or end_index == -1 or end_index < start_index:
            raise DomainError(code="E-1004", message="分图确认失败：模型返回格式异常，请重试", status_code=503)
        try:
            payload = json.loads(text[start_index : end_index + 1])
        except json.JSONDecodeError as error:
            raise DomainError(code="E-1004", message="分图确认失败：模型返回格式异常，请重试", status_code=503) from error
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or len(items) < image_count:
            raise DomainError(code="E-1004", message="分图确认失败：模型返回的分图数量不足，请重试", status_code=503)
        valid_source_ids = {asset_id for asset_id in source_asset_ids if asset_id}
        candidate_locations = self._normalize_asset_list(
            asset_candidates.get("locations") if isinstance(asset_candidates, dict) else [],
        )
        normalized_items: list[dict[str, Any]] = []
        for index, raw_item in enumerate(items[:image_count], start=1):
            item = raw_item if isinstance(raw_item, dict) else {}
            focus_description = str(item.get("focus_description") or "").strip()
            if not focus_description:
                raise DomainError(code="E-1004", message="分图确认失败：缺少每张图重点内容描述，请重试", status_code=503)
            slot_index_raw = item.get("slot_index")
            slot_index = slot_index_raw if isinstance(slot_index_raw, int) and slot_index_raw > 0 else index
            source_ids = self._normalize_source_asset_ids(item.get("source_asset_ids"), valid_source_ids)
            if not source_ids:
                raise DomainError(code="E-1004", message="分图确认失败：缺少可追溯素材来源，请重试", status_code=503)
            locations = self._normalize_asset_list(item.get("locations"))
            if not locations and candidate_locations:
                locations = candidate_locations[:4]
            scenes = self._normalize_asset_list(item.get("scenes"))
            foods = self._normalize_asset_list(item.get("foods"))
            keywords = self._normalize_asset_list(item.get("keywords"))
            normalized_items.append(
                {
                    "slot_index": slot_index,
                    "focus_title": str(item.get("focus_title") or f"第{index}张重点").strip() or f"第{index}张重点",
                    "focus_description": focus_description,
                    "locations": locations[:8],
                    "scenes": scenes[:10],
                    "foods": foods[:10],
                    "keywords": keywords[:15],
                    "source_asset_ids": source_ids[:5],
                    "confirmed": False,
                }
            )
        normalized_items.sort(key=lambda entry: int(entry.get("slot_index") or 0))
        for index, item in enumerate(normalized_items, start=1):
            item["slot_index"] = index
        return normalized_items

    def _normalize_source_asset_ids(self, values: Any, valid_source_ids: set[str]) -> list[str]:
        if not isinstance(values, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            if not text or text in seen or text not in valid_source_ids:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized

    def _build_asset_confirm_reply(self, state: dict[str, Any]) -> str:
        allocation_plan = state.get("allocation_plan") if isinstance(state.get("allocation_plan"), list) else []
        if not allocation_plan:
            return f"{ASSET_CONFIRM_HINT}\n\n暂未生成分图建议，请补充需求后继续。"
        sections = [ASSET_CONFIRM_HINT]
        for plan_item in allocation_plan:
            slot_index = int(plan_item.get("slot_index") or 0)
            focus_title = str(plan_item.get("focus_title") or "").strip()
            focus_description = str(plan_item.get("focus_description") or "").strip()
            locations = "、".join(plan_item.get("locations") or []) or "无"
            scenes = "、".join(plan_item.get("scenes") or []) or "无"
            foods = "、".join(plan_item.get("foods") or []) or "无"
            line_title = f"第{slot_index}张" if slot_index > 0 else "分图建议"
            if focus_title:
                line_title = f"{line_title}（{focus_title}）"
            sections.append(
                f"{line_title}：{focus_description}\n"
                f"- 地点：{locations}\n"
                f"- 景点：{scenes}\n"
                f"- 美食：{foods}"
            )
        sections.append("如果你有指定分配，直接回复“第几张做什么”；如果你说“随便”，我会按主题自动分配。")
        return "\n\n".join(sections)

    def _mark_allocation_plan_confirmed(self, allocation_plan: Any) -> list[dict[str, Any]]:
        if not isinstance(allocation_plan, list):
            return []
        confirmed_items: list[dict[str, Any]] = []
        for item in allocation_plan:
            if not isinstance(item, dict):
                continue
            confirmed_items.append({**item, "confirmed": True})
        return confirmed_items
