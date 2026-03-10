from __future__ import annotations

from typing import Any

from backend.app.infra.db import Database


class ConfigRepository:
    def __init__(self, db: Database):
        self.db = db

    def get_model_routing(self) -> dict[str, Any] | None:
        row = self.db.fetch_one(
            """
            SELECT id, image_model_provider_id, image_model_name,
                   text_model_provider_id, text_model_name,
                   transcript_model_provider_id, transcript_model_name,
                   updated_at
            FROM model_routing_config
            WHERE id = 'default'
            """
        )
        if not row:
            return None
        if not row.get("transcript_model_provider_id") or not row.get("transcript_model_name"):
            return None
        return {
            "image_model": {
                "provider_id": row["image_model_provider_id"],
                "model_name": row["image_model_name"],
            },
            "text_model": {
                "provider_id": row["text_model_provider_id"],
                "model_name": row["text_model_name"],
            },
            "transcript_model": {
                "provider_id": row["transcript_model_provider_id"],
                "model_name": row["transcript_model_name"],
            },
            "updated_at": row["updated_at"],
        }

    def upsert_model_routing(self, payload: dict[str, Any], updated_at: str) -> dict[str, Any]:
        self.db.execute(
            """
            INSERT INTO model_routing_config (
              id, image_model_provider_id, image_model_name,
              text_model_provider_id, text_model_name,
              transcript_model_provider_id, transcript_model_name,
              updated_at
            ) VALUES ('default', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              image_model_provider_id=excluded.image_model_provider_id,
              image_model_name=excluded.image_model_name,
              text_model_provider_id=excluded.text_model_provider_id,
              text_model_name=excluded.text_model_name,
              transcript_model_provider_id=excluded.transcript_model_provider_id,
              transcript_model_name=excluded.transcript_model_name,
              updated_at=excluded.updated_at
            """,
            (
                payload["image_model"]["provider_id"],
                payload["image_model"]["model_name"],
                payload["text_model"]["provider_id"],
                payload["text_model"]["model_name"],
                payload["transcript_model"]["provider_id"],
                payload["transcript_model"]["model_name"],
                updated_at,
            ),
        )
        return self.get_model_routing() or {}
