from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_path: Path
    storage_dir: Path
    public_base_url: str
    export_font_paths: tuple[str, ...]


def load_settings() -> Settings:
    db_path = Path(os.getenv("SAVORY_CANVAS_DB_PATH", "backend/data/savory_canvas.db"))
    storage_dir = Path(os.getenv("SAVORY_CANVAS_STORAGE_DIR", "backend/storage"))
    public_base_url = os.getenv("SAVORY_CANVAS_PUBLIC_BASE_URL", "http://127.0.0.1:8887")
    raw_font_paths = os.getenv("SAVORY_CANVAS_EXPORT_FONT_PATHS", "")
    export_font_paths = tuple(path.strip() for path in raw_font_paths.split(";") if path.strip())
    return Settings(
        db_path=db_path,
        storage_dir=storage_dir,
        public_base_url=public_base_url.rstrip("/"),
        export_font_paths=export_font_paths,
    )
