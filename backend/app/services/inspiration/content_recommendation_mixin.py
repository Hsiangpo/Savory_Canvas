from __future__ import annotations

from typing import Any

from backend.app.core.errors import not_found


class InspirationContentRecommendationMixin:
    _CITY_COMBO_CATALOG: dict[str, list[dict[str, Any]]] = {
        "food": [
            {"city": "顺德", "scenes": ["老街骑楼"], "foods": ["双皮奶", "鱼生"], "summary": "老街烟火 + 经典粤味，适合做温暖治愈的吃喝路线。"},
            {"city": "扬州", "scenes": ["瘦西湖沿岸"], "foods": ["扬州早茶", "狮子头"], "summary": "水岸慢游 + 细腻淮扬味，适合做精致攻略型图文。"},
            {"city": "成都", "scenes": ["宽窄巷子"], "foods": ["回锅肉", "盖碗茶"], "summary": "市井街巷 + 川味烟火，适合做生活感很强的城市美食手账。"},
            {"city": "广州", "scenes": ["西关骑楼"], "foods": ["早茶", "艇仔粥"], "summary": "老广街巷 + 早茶文化，适合做复古信息图路线。"},
        ],
        "scenic": [
            {"city": "苏州", "scenes": ["拙政园", "平江路"], "foods": [], "summary": "园林层次 + 水巷步行线，适合做江南气质的漫游图解。"},
            {"city": "泉州", "scenes": ["开元寺", "西街"], "foods": [], "summary": "古城宗教建筑 + 市井步行街，适合做文化漫游路线。"},
            {"city": "丽江", "scenes": ["古城", "玉龙雪山远眺"], "foods": [], "summary": "古镇纹理 + 山景层次，适合做旅行手账型海报。"},
            {"city": "重庆", "scenes": ["山城步道", "洪崖洞夜景"], "foods": [], "summary": "高低落差 + 夜景氛围，适合做城市动线很强的图解。"},
        ],
        "food_scenic": [
            {"city": "苏州", "scenes": ["拙政园", "平江路"], "foods": ["苏式点心", "松鼠桂鱼"], "summary": "园林漫游 + 苏式点心，画面细腻、非常适合复古旅行手账。"},
            {"city": "厦门", "scenes": ["鼓浪屿", "骑楼街区"], "foods": ["沙茶面", "花生汤"], "summary": "海岛步行线 + 闽南小吃，适合轻松治愈的城市图解。"},
            {"city": "扬州", "scenes": ["瘦西湖", "东关街"], "foods": ["扬州早茶", "烫干丝"], "summary": "水岸园林 + 早茶节奏，适合做精致温润的攻略海报。"},
            {"city": "成都", "scenes": ["人民公园", "宽窄巷子"], "foods": ["回锅肉", "糖油果子"], "summary": "公园慢生活 + 川味烟火，适合做生活感浓的城市手账。"},
        ],
    }

    def recommend_city_content_combos(self, *, session_id: str, limit: int = 2) -> dict[str, Any]:
        session = self.session_repo.get(session_id)
        if not session:
            raise not_found("会话", session_id)
        state = self._ensure_state(session_id)
        content_mode = str(session.get("content_mode") or state.get("content_mode") or "food_scenic")
        catalog = list(self._CITY_COMBO_CATALOG.get(content_mode) or self._CITY_COMBO_CATALOG["food_scenic"])
        existing_locations = {
            str(item).strip()
            for item in ((state.get("asset_candidates") or {}).get("locations") or [])
            if str(item).strip()
        }
        if existing_locations:
            filtered = [item for item in catalog if item.get("city") not in existing_locations]
            if filtered:
                catalog = filtered
        normalized_limit = max(1, min(4, int(limit or 2)))
        items = []
        for combo in catalog[:normalized_limit]:
            items.append(
                {
                    "city": combo["city"],
                    "scenes": list(combo.get("scenes") or []),
                    "foods": list(combo.get("foods") or []),
                    "summary": str(combo.get("summary") or "").strip(),
                }
            )
        return {"items": items}
