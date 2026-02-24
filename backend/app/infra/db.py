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

    @property
    def db_path(self) -> Path:
        return self._db_path

    def initialize(self, migration_path: Path) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        script = migration_path.read_text(encoding="utf-8")
        with self.transaction() as conn:
            conn.executescript(script)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._db_path), check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self.transaction() as conn:
            conn.execute(sql, params)

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.transaction() as conn:
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.transaction() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)
