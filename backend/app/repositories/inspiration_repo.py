from __future__ import annotations

from typing import Any

from backend.app.infra.db import Database, json_dumps, json_loads


class InspirationRepository:
    def __init__(self, db: Database):
        self.db = db

    def get_state(self, session_id: str) -> dict[str, Any] | None:
        row = self.db.fetch_one(
            """
            SELECT session_id, stage, style_stage, is_locked AS locked, image_count, style_prompt,
                   style_payload, asset_candidates, draft_style_id, transcript_seen_ids, updated_at
            FROM inspiration_state
            WHERE session_id = ?
            """,
            (session_id,),
        )
        return self._deserialize_state(row) if row else None

    def upsert_state(self, state: dict[str, Any]) -> dict[str, Any]:
        self.db.execute(
            """
            INSERT INTO inspiration_state (
                session_id, stage, style_stage, is_locked, image_count, style_prompt,
                style_payload, asset_candidates, draft_style_id, transcript_seen_ids, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                stage=excluded.stage,
                style_stage=excluded.style_stage,
                is_locked=excluded.is_locked,
                image_count=excluded.image_count,
                style_prompt=excluded.style_prompt,
                style_payload=excluded.style_payload,
                asset_candidates=excluded.asset_candidates,
                draft_style_id=excluded.draft_style_id,
                transcript_seen_ids=excluded.transcript_seen_ids,
                updated_at=excluded.updated_at
            """,
            (
                state["session_id"],
                state["stage"],
                state["style_stage"],
                1 if state["locked"] else 0,
                state.get("image_count"),
                state.get("style_prompt"),
                json_dumps(state.get("style_payload", {})),
                json_dumps(state.get("asset_candidates", {})),
                state.get("draft_style_id"),
                json_dumps(state.get("transcript_seen_ids", [])),
                state["updated_at"],
            ),
        )
        return self.get_state(state["session_id"]) or state

    def add_message(self, message: dict[str, Any]) -> dict[str, Any]:
        self.db.execute(
            """
            INSERT INTO inspiration_message (
                id, session_id, sender, text, attachments, options, asset_candidates, style_context, stage, fallback_used, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message["id"],
                message["session_id"],
                message["role"],
                message["content"],
                json_dumps(message.get("attachments", [])),
                json_dumps(message["options"]) if message.get("options") is not None else None,
                json_dumps(message.get("asset_candidates")) if message.get("asset_candidates") is not None else None,
                json_dumps(message.get("style_context")) if message.get("style_context") is not None else None,
                message.get("stage", "style_collecting"),
                1 if message.get("fallback_used") else 0,
                message["created_at"],
            ),
        )
        return message

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """
            SELECT id, session_id, sender, text, attachments, options, asset_candidates, style_context, stage, fallback_used, created_at
            FROM inspiration_message
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        )
        return [self._deserialize_message(row) for row in rows]

    def _deserialize_state(self, row: dict[str, Any]) -> dict[str, Any]:
        row["locked"] = bool(row["locked"])
        row["style_payload"] = json_loads(row.get("style_payload"), default={})
        row["asset_candidates"] = json_loads(row.get("asset_candidates"), default={})
        row["transcript_seen_ids"] = json_loads(row.get("transcript_seen_ids"), default=[])
        return row

    def _deserialize_message(self, row: dict[str, Any]) -> dict[str, Any]:
        attachments = self._normalize_attachments(json_loads(row.get("attachments"), default=[]))
        return {
            "id": row["id"],
            "role": row.get("sender") or "assistant",
            "content": row.get("text") or "",
            "options": json_loads(row.get("options"), default=None),
            "fallback_used": bool(row["fallback_used"]),
            "attachments": attachments,
            "asset_candidates": json_loads(row.get("asset_candidates"), default=None),
            "style_context": json_loads(row.get("style_context"), default=None),
            "created_at": row["created_at"],
        }

    def _normalize_attachments(self, attachments: Any) -> list[dict[str, Any]]:
        if not isinstance(attachments, list):
            return []
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(attachments, start=1):
            if not isinstance(item, dict):
                continue
            asset_id = item.get("asset_id")
            attachment_id = item.get("id") or asset_id or f"attachment-{index}"
            attachment_type = item.get("type") or "text"
            status = item.get("status") or "ready"
            usage_type = item.get("usage_type")
            normalized.append(
                {
                    "id": attachment_id,
                    "asset_id": asset_id,
                    "type": attachment_type,
                    "name": item.get("name"),
                    "preview_url": item.get("preview_url"),
                    "status": status,
                    "usage_type": usage_type if usage_type in {"style_reference", "content_asset"} else None,
                }
            )
        return normalized
