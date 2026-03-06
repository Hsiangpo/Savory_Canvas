from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class Database:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._lock = threading.RLock()
        self._local = threading.local()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def initialize(self, migration_path: Path) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        script = migration_path.read_text(encoding="utf-8")
        with self.transaction() as conn:
            conn.executescript(script)
            self._apply_compatibility_migrations(conn)

    def _apply_compatibility_migrations(self, conn: sqlite3.Connection) -> None:
        table_columns: dict[str, list[tuple[str, str]]] = {
            "inspiration_state": [
                ("style_stage", "TEXT NOT NULL DEFAULT 'painting_style'"),
                ("asset_candidates", "TEXT NOT NULL DEFAULT '{}'"),
                ("allocation_plan", "TEXT NOT NULL DEFAULT '[]'"),
                ("draft_style_id", "TEXT"),
                ("requirement_ready", "INTEGER NOT NULL DEFAULT 1"),
                ("transcript_seen_ids", "TEXT NOT NULL DEFAULT '[]'"),
                ("progress", "INTEGER"),
                ("progress_label", "TEXT"),
                ("active_job_id", "TEXT"),
                ("updated_at", "TEXT NOT NULL DEFAULT ''"),
            ],
            "inspiration_message": [
                ("options", "TEXT"),
                ("asset_candidates", "TEXT"),
                ("style_context", "TEXT"),
                ("stage", "TEXT NOT NULL DEFAULT 'style_collecting'"),
                ("fallback_used", "INTEGER NOT NULL DEFAULT 0"),
            ],
            "generation_job": [
                ("progress_percent", "INTEGER NOT NULL DEFAULT 0"),
                ("current_stage", "TEXT NOT NULL DEFAULT 'asset_extract'"),
                ("stage_message", "TEXT NOT NULL DEFAULT '任务已创建，等待执行'"),
                ("error_code", "TEXT"),
                ("error_message", "TEXT"),
            ],
            "image_result": [
                ("asset_refs", "TEXT NOT NULL DEFAULT '[]'"),
                ("prompt_text", "TEXT NOT NULL DEFAULT ''"),
            ],
            "copy_result": [
                ("intro", "TEXT NOT NULL DEFAULT ''"),
                ("guide_sections", "TEXT NOT NULL DEFAULT '[]'"),
                ("ending", "TEXT NOT NULL DEFAULT ''"),
                ("full_text", "TEXT NOT NULL DEFAULT ''"),
            ],
        }
        for table_name, columns in table_columns.items():
            self._ensure_table_columns(conn, table_name, columns)

    def _ensure_table_columns(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        columns: list[tuple[str, str]],
    ) -> None:
        if not self._table_exists(conn, table_name):
            return
        existing_columns = self._list_table_columns(conn, table_name)
        for column_name, column_definition in columns:
            if column_name in existing_columns:
                continue
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return row is not None

    def _list_table_columns(self, conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._db_path), check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _thread_connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._connect()
            self._local.connection = connection
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._thread_connection()
            manage_transaction = not connection.in_transaction
            try:
                yield connection
                if manage_transaction:
                    connection.commit()
            except Exception:
                if manage_transaction and connection.in_transaction:
                    connection.rollback()
                raise

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self.transaction() as conn:
            conn.execute(sql, params)

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        conn = self._thread_connection()
        with self._lock:
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        conn = self._thread_connection()
        with self._lock:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)
