from __future__ import annotations

import asyncio
import threading

from backend.app.core.utils import new_id, now_iso
from backend.app.repositories.asset_repo import AssetRepository


class TranscriptWorker:
    def __init__(self, asset_repo: AssetRepository):
        self.asset_repo = asset_repo

    def schedule(self, session_id: str, asset_id: str, file_name: str, file_path: str) -> None:
        threading.Thread(target=lambda: asyncio.run(self._run(session_id, asset_id, file_name, file_path)), daemon=True).start()

    async def _run(self, session_id: str, asset_id: str, file_name: str, file_path: str) -> None:
        try:
            await asyncio.sleep(0.08)
            text = f"已完成视频转写：{file_name}"
            segments = [
                {"start": 0, "end": 5, "text": text},
                {"start": 5, "end": 10, "text": "请根据内容生成图文"},
            ]
            self.asset_repo.update_transcript(
                asset_id=asset_id,
                status="ready",
                text=text,
                segments=segments,
                error_code=None,
                error_message=None,
                updated_at=now_iso(),
            )
            self.asset_repo.update_status(asset_id, "ready")
            self.asset_repo.create(
                {
                    "id": new_id(),
                    "session_id": session_id,
                    "asset_type": "transcript",
                    "content": text,
                    "file_path": file_path,
                    "status": "ready",
                    "created_at": now_iso(),
                }
            )
        except Exception:
            self.asset_repo.update_status(asset_id, "failed")
            self.asset_repo.update_transcript(
                asset_id=asset_id,
                status="failed",
                text=None,
                segments=None,
                error_code="E-1001",
                error_message="视频转写失败",
                updated_at=now_iso(),
            )

