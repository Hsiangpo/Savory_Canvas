from __future__ import annotations

from typing import Any

from backend.app.infra.db import Database


class SessionRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, session: dict[str, Any]) -> dict[str, Any]:
        self.db.execute(
            """
            INSERT INTO session (id, title, content_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session["id"],
                session["title"],
                session["content_mode"],
                session["created_at"],
                session["updated_at"],
            ),
        )
        return session

    def list_all(self) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT id, title, content_mode, created_at, updated_at
            FROM session
            ORDER BY created_at DESC
            """
        )

    def get(self, session_id: str) -> dict[str, Any] | None:
        return self.db.fetch_one(
            """
            SELECT id, title, content_mode, created_at, updated_at
            FROM session
            WHERE id = ?
            """,
            (session_id,),
        )

    def update_session(
        self,
        session_id: str,
        title: str,
        content_mode: str | None,
        updated_at: str,
    ) -> dict[str, Any] | None:
        found = self.get(session_id)
        if not found:
            return None
        next_content_mode = content_mode or found["content_mode"]
        self.db.execute(
            """
            UPDATE session
            SET title = ?, content_mode = ?, updated_at = ?
            WHERE id = ?
            """,
            (title, next_content_mode, updated_at, session_id),
        )
        return self.get(session_id)

    def delete_session(self, session_id: str) -> bool:
        found = self.get(session_id)
        if not found:
            return False
        self.db.execute("DELETE FROM session WHERE id = ?", (session_id,))
        return True
