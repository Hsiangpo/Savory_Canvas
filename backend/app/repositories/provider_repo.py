from __future__ import annotations

from typing import Any

from backend.app.core.secrets import decrypt_text
from backend.app.infra.db import Database


class ProviderRepository:
    def __init__(self, db: Database):
        self.db = db

    def _get_raw(self, provider_id: str) -> dict[str, Any] | None:
        return self.db.fetch_one(
            """
            SELECT id, name, base_url, api_key, api_key_masked,
                   api_protocol, enabled, created_at, updated_at
            FROM provider_config
            WHERE id = ?
            """,
            (provider_id,),
        )

    def create(self, provider: dict[str, Any]) -> dict[str, Any]:
        self.db.execute(
            """
            INSERT INTO provider_config (
              id, name, base_url, api_key, api_key_masked,
              api_protocol, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider["id"],
                provider["name"],
                provider["base_url"],
                provider["api_key"],
                provider["api_key_masked"],
                provider["api_protocol"],
                1 if provider["enabled"] else 0,
                provider["created_at"],
                provider["updated_at"],
            ),
        )
        return provider

    def get(self, provider_id: str) -> dict[str, Any] | None:
        row = self._get_raw(provider_id)
        if row:
            row["enabled"] = bool(row["enabled"])
            row["api_key"] = decrypt_text(row["api_key"])
        return row

    def list_all(self) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """
            SELECT id, name, base_url, api_key, api_key_masked,
                   api_protocol, enabled, created_at, updated_at
            FROM provider_config
            ORDER BY created_at DESC
            """
        )
        for row in rows:
            row["enabled"] = bool(row["enabled"])
            row["api_key"] = decrypt_text(row["api_key"])
        return rows

    def update(self, provider_id: str, fields: dict[str, Any], updated_at: str) -> dict[str, Any] | None:
        provider = self._get_raw(provider_id)
        if not provider:
            return None
        merged = {
            **provider,
            **fields,
            "updated_at": updated_at,
        }
        self.db.execute(
            """
            UPDATE provider_config
            SET name = ?, base_url = ?, api_key = ?, api_key_masked = ?,
                api_protocol = ?, enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                merged["name"],
                merged["base_url"],
                merged["api_key"],
                merged["api_key_masked"],
                merged["api_protocol"],
                1 if merged["enabled"] else 0,
                merged["updated_at"],
                provider_id,
            ),
        )
        return self.get(provider_id)

    def delete(self, provider_id: str) -> bool:
        found = self.get(provider_id)
        if not found:
            return False
        self.db.execute("DELETE FROM provider_config WHERE id = ?", (provider_id,))
        return True
