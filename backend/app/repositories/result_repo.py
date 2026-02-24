from __future__ import annotations

from typing import Any

from backend.app.infra.db import Database, json_dumps, json_loads


class ResultRepository:
    def __init__(self, db: Database):
        self.db = db

    def add_image(self, result: dict[str, Any]) -> dict[str, Any]:
        self.db.execute(
            """
            INSERT INTO image_result (id, job_id, image_index, asset_refs, prompt_text, image_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result["id"],
                result["job_id"],
                result["image_index"],
                json_dumps(result.get("asset_refs", [])),
                result["prompt_text"],
                result["image_path"],
                result["created_at"],
            ),
        )
        return result

    def list_images(self, job_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """
            SELECT image_index, asset_refs, prompt_text, image_path
            FROM image_result
            WHERE job_id = ?
            ORDER BY image_index ASC
            """,
            (job_id,),
        )
        for row in rows:
            row["asset_refs"] = json_loads(row.get("asset_refs"), default=[])
        return rows

    def upsert_copy(self, copy_result: dict[str, Any]) -> dict[str, Any]:
        self.db.execute(
            """
            INSERT INTO copy_result (id, job_id, title, intro, guide_sections, ending, full_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
              title=excluded.title,
              intro=excluded.intro,
              guide_sections=excluded.guide_sections,
              ending=excluded.ending,
              full_text=excluded.full_text
            """,
            (
                copy_result["id"],
                copy_result["job_id"],
                copy_result["title"],
                copy_result["intro"],
                json_dumps(copy_result["guide_sections"]),
                copy_result["ending"],
                copy_result["full_text"],
                copy_result["created_at"],
            ),
        )
        return copy_result

    def get_copy(self, job_id: str) -> dict[str, Any] | None:
        row = self.db.fetch_one(
            """
            SELECT title, intro, guide_sections, ending, full_text
            FROM copy_result
            WHERE job_id = ?
            """,
            (job_id,),
        )
        if not row:
            return None
        row["guide_sections"] = json_loads(row.get("guide_sections"), default=[])
        return row

    def upsert_asset_breakdown(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.db.execute(
            """
            INSERT INTO generation_asset_breakdown (
                job_id, session_id, content_mode, source_assets, extracted, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
              session_id=excluded.session_id,
              content_mode=excluded.content_mode,
              source_assets=excluded.source_assets,
              extracted=excluded.extracted,
              created_at=excluded.created_at
            """,
            (
                payload["job_id"],
                payload["session_id"],
                payload["content_mode"],
                json_dumps(payload.get("source_assets", [])),
                json_dumps(payload.get("extracted", {})),
                payload["created_at"],
            ),
        )
        return payload

    def get_asset_breakdown(self, job_id: str) -> dict[str, Any] | None:
        row = self.db.fetch_one(
            """
            SELECT job_id, session_id, content_mode, source_assets, extracted, created_at
            FROM generation_asset_breakdown
            WHERE job_id = ?
            """,
            (job_id,),
        )
        if not row:
            return None
        row["source_assets"] = json_loads(row.get("source_assets"), default=[])
        row["extracted"] = json_loads(row.get("extracted"), default={})
        return row
