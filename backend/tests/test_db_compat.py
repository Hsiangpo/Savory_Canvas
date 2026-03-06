from __future__ import annotations

import sqlite3
import sys
import threading
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
    assert "progress" in state_columns
    assert "progress_label" in state_columns
    assert "active_job_id" in state_columns


def test_connect_enables_wal_busy_timeout_and_normal_synchronous(tmp_path):
    db_path = tmp_path / "pragma.db"
    migration_path = Path(__file__).resolve().parents[1] / "migrations" / "001_init.sql"
    database = Database(db_path)
    database.initialize(migration_path)

    with database.transaction() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]

    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) == 5000
    assert int(synchronous) == 1


def test_fetch_operations_reuse_same_thread_local_connection(tmp_path, monkeypatch):
    db_path = tmp_path / "reuse.db"
    migration_path = Path(__file__).resolve().parents[1] / "migrations" / "001_init.sql"
    database = Database(db_path)
    database.initialize(migration_path)

    created_connections: list[int] = []
    original_connect = database._connect

    def tracked_connect():
        connection = original_connect()
        created_connections.append(id(connection))
        return connection

    monkeypatch.setattr(database, "_connect", tracked_connect)
    monkeypatch.setattr(database, "_local", threading.local())
    database.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")
    database.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")

    assert len(created_connections) == 1
