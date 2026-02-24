from __future__ import annotations

from typing import Any

from backend.app.infra.db import Database, json_dumps, json_loads


class AssetRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, asset: dict[str, Any]) -> dict[str, Any]:
        self.db.execute(
            """
            INSERT INTO asset (id, session_id, asset_type, content, file_path, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset["id"],
                asset["session_id"],
                asset["asset_type"],
                asset.get("content"),
                asset.get("file_path"),
                asset["status"],
                asset["created_at"],
            ),
        )
        return asset

    def get(self, asset_id: str) -> dict[str, Any] | None:
        return self.db.fetch_one(
            """
            SELECT id, session_id, asset_type, content, file_path, status, created_at
            FROM asset
            WHERE id = ?
            """,
            (asset_id,),
        )

    def list_by_session(self, session_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT id, session_id, asset_type, content, file_path, status, created_at
            FROM asset
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        )

    def update_status(self, asset_id: str, status: str) -> None:
        self.db.execute(
            "UPDATE asset SET status = ? WHERE id = ?",
            (status, asset_id),
        )

    def create_transcript(self, asset_id: str, status: str, updated_at: str) -> None:
        self.db.execute(
            """
            INSERT INTO transcript_result (asset_id, status, text, segments, error_code, error_message, updated_at)
            VALUES (?, ?, NULL, NULL, NULL, NULL, ?)
            """,
            (asset_id, status, updated_at),
        )

    def update_transcript(
        self,
        asset_id: str,
        status: str,
        text: str | None,
        segments: list[dict[str, Any]] | None,
        error_code: str | None,
        error_message: str | None,
        updated_at: str,
    ) -> None:
        self.db.execute(
            """
            UPDATE transcript_result
            SET status = ?, text = ?, segments = ?, error_code = ?, error_message = ?, updated_at = ?
            WHERE asset_id = ?
            """,
            (
                status,
                text,
                json_dumps(segments) if segments is not None else None,
                error_code,
                error_message,
                updated_at,
                asset_id,
            ),
        )

    def get_transcript(self, asset_id: str) -> dict[str, Any] | None:
        row = self.db.fetch_one(
            """
            SELECT asset_id, status, text, segments, error_code, error_message
            FROM transcript_result
            WHERE asset_id = ?
            """,
            (asset_id,),
        )
        if not row:
            return None
        row["segments"] = json_loads(row.get("segments"), default=[])
        return row
