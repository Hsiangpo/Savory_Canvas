from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT_PATH = Path(__file__).resolve().parents[2]
if str(ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(ROOT_PATH))

from backend.app.infra.db import Database


def test_initialize_adds_missing_columns_for_legacy_inspiration_tables(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE inspiration_state (
              session_id TEXT PRIMARY KEY,
              stage TEXT NOT NULL,
              is_locked INTEGER NOT NULL,
              image_count INTEGER,
              style_prompt TEXT,
              style_payload TEXT NOT NULL
            );

            CREATE TABLE inspiration_message (
              id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL,
              sender TEXT NOT NULL,
              text TEXT NOT NULL,
              attachments TEXT NOT NULL,
              options TEXT,
              stage TEXT NOT NULL,
              fallback_used INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )

    migration_path = Path(__file__).resolve().parents[1] / "migrations" / "001_init.sql"
    database = Database(db_path)
    database.initialize(migration_path)

    with database.transaction() as conn:
        message_columns = {row["name"] for row in conn.execute("PRAGMA table_info(inspiration_message)").fetchall()}
        state_columns = {row["name"] for row in conn.execute("PRAGMA table_info(inspiration_state)").fetchall()}

    assert "asset_candidates" in message_columns
    assert "style_context" in message_columns
    assert "asset_candidates" in state_columns
    assert "transcript_seen_ids" in state_columns
