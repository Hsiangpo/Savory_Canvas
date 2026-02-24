from __future__ import annotations

from typing import Any

from backend.app.infra.db import Database


class ExportRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, task: dict[str, Any]) -> dict[str, Any]:
        self.db.execute(
            """
            INSERT INTO export_task (
                id, session_id, job_id, export_format, status,
                file_path, error_code, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task["id"],
                task["session_id"],
                task["job_id"],
                task["export_format"],
                task["status"],
                task.get("file_path"),
                task.get("error_code"),
                task.get("error_message"),
                task["created_at"],
            ),
        )
        return task

    def get(self, export_id: str) -> dict[str, Any] | None:
        row = self.db.fetch_one(
            """
            SELECT id, session_id, job_id, export_format, status,
                   file_path, error_code, error_message, created_at
            FROM export_task
            WHERE id = ?
            """,
            (export_id,),
        )
        if row and row.get("file_path"):
            row["file_url"] = row.pop("file_path")
        elif row:
            row["file_url"] = None
            row.pop("file_path", None)
        return row

    def list_by_session(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """
            SELECT id, session_id, job_id, export_format, status,
                   file_path, error_code, error_message, created_at
            FROM export_task
            WHERE session_id = ?
            ORDER BY created_at DESC
            """,
            (session_id,),
        )
        for row in rows:
            row["file_url"] = row.pop("file_path")
        return rows

    def update_state(
        self,
        export_id: str,
        *,
        status: str,
        file_path: str | None,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        self.db.execute(
            """
            UPDATE export_task
            SET status = ?, file_path = ?, error_code = ?, error_message = ?
            WHERE id = ?
            """,
            (status, file_path, error_code, error_message, export_id),
        )
