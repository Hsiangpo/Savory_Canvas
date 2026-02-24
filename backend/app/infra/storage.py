from __future__ import annotations

from pathlib import Path


class Storage:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.ensure_dirs()

    def ensure_dirs(self) -> None:
        for name in ("videos", "generated", "exports", "images"):
            (self.base_dir / name).mkdir(parents=True, exist_ok=True)

    def save_video(self, filename: str, content: bytes) -> str:
        path = self.base_dir / "videos" / filename
        path.write_bytes(content)
        return str(path)

    def save_image(self, filename: str, content: bytes) -> str:
        path = self.base_dir / "images" / filename
        path.write_bytes(content)
        return str(path)

    def save_generated_image(self, filename: str, content: bytes) -> str:
        path = self.base_dir / "generated" / filename
        path.write_bytes(content)
        return str(path)

    def save_export(self, filename: str, content: str) -> str:
        path = self.base_dir / "exports" / filename
        path.write_text(content, encoding="utf-8")
        return str(path)
