from __future__ import annotations

from typing import Any

from backend.app.infra.db import Database, json_dumps, json_loads


class StyleRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, profile: dict[str, Any]) -> dict[str, Any]:
        self.db.execute(
            """
            INSERT INTO style_profile (id, session_id, name, style_payload, is_builtin, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile["id"],
                profile.get("session_id"),
                profile["name"],
                json_dumps(profile["style_payload"]),
                1 if profile["is_builtin"] else 0,
                profile["created_at"],
                profile["updated_at"],
            ),
        )
        return profile

    def list_all(self) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """
            SELECT id, session_id, name, style_payload, is_builtin, created_at, updated_at
            FROM style_profile
            ORDER BY created_at DESC
            """
        )
        return [self._deserialize(row) for row in rows]

    def get(self, style_id: str) -> dict[str, Any] | None:
        row = self.db.fetch_one(
            """
            SELECT id, session_id, name, style_payload, is_builtin, created_at, updated_at
            FROM style_profile
            WHERE id = ?
            """,
            (style_id,),
        )
        return self._deserialize(row) if row else None

    def update_name(self, style_id: str, name: str, updated_at: str) -> dict[str, Any] | None:
        self.db.execute(
            "UPDATE style_profile SET name = ?, updated_at = ? WHERE id = ?",
            (name, updated_at, style_id),
        )
        return self.get(style_id)

    def update_payload(self, style_id: str, style_payload: dict[str, Any], updated_at: str) -> dict[str, Any] | None:
        self.db.execute(
            "UPDATE style_profile SET style_payload = ?, updated_at = ? WHERE id = ?",
            (json_dumps(style_payload), updated_at, style_id),
        )
        return self.get(style_id)

    def delete(self, style_id: str) -> bool:
        before = self.get(style_id)
        if not before:
            return False
        self.db.execute("DELETE FROM style_profile WHERE id = ?", (style_id,))
        return True

    def _deserialize(self, row: dict[str, Any]) -> dict[str, Any]:
        row["style_payload"] = json_loads(row.get("style_payload"), default={})
        row["is_builtin"] = bool(row["is_builtin"])
        return row
