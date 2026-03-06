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
        relative_path = Path("videos") / filename
        path = self.base_dir / relative_path
        path.write_bytes(content)
        return relative_path.as_posix()

    def save_image(self, filename: str, content: bytes) -> str:
        relative_path = Path("images") / filename
        path = self.base_dir / relative_path
        path.write_bytes(content)
        return relative_path.as_posix()

    def save_generated_image(self, filename: str, content: bytes) -> str:
        relative_path = Path("generated") / filename
        path = self.base_dir / relative_path
        path.write_bytes(content)
        return relative_path.as_posix()

    def save_export(self, filename: str, content: str | bytes) -> str:
        relative_path = Path("exports") / filename
        path = self.base_dir / relative_path
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return relative_path.as_posix()
