from __future__ import annotations

from typing import Any

from backend.app.core.errors import DomainError, not_found
from backend.app.core.utils import new_id, now_iso
from backend.app.repositories.asset_repo import AssetRepository
from backend.app.repositories.session_repo import SessionRepository
from backend.app.services.model_service import ModelService
from backend.app.workers.transcript_worker import TranscriptWorker


class TranscriptService:
    def __init__(
        self,
        asset_repo: AssetRepository,
        session_repo: SessionRepository,
        model_service: ModelService,
        worker: TranscriptWorker,
    ):
        self.asset_repo = asset_repo
        self.session_repo = session_repo
        self.model_service = model_service
        self.worker = worker

    def ensure_video_upload_ready(self, session_id: str) -> None:
        if not self.session_repo.get(session_id):
            raise not_found("会话", session_id)
        self.model_service.require_transcript_target()

    def create_video_asset(self, session_id: str, file_path: str, file_name: str) -> dict[str, Any]:
        if not self.session_repo.get(session_id):
            raise not_found("会话", session_id)
        provider, model_name = self.model_service.require_transcript_target()
        now = now_iso()
        asset = {
            "id": new_id(),
            "session_id": session_id,
            "asset_type": "video",
            "content": file_name,
            "file_path": file_path,
            "status": "processing",
            "created_at": now,
        }
        self.asset_repo.create(asset)
        self.asset_repo.create_transcript(asset["id"], "processing", now)
        self.worker.schedule(
            session_id=session_id,
            asset_id=asset["id"],
            file_name=file_name,
            file_path=file_path,
            provider=provider,
            model_name=model_name,
        )
        return asset

    def get_transcript(self, asset_id: str) -> dict[str, Any]:
        asset = self.asset_repo.get(asset_id)
        if not asset:
            raise not_found("素材", asset_id)
        transcript = self.asset_repo.get_transcript(asset_id)
        if not transcript:
            return {"asset_id": asset_id, "status": "processing", "text": "", "segments": []}
        return {
            "asset_id": asset_id,
            "status": transcript["status"],
            "text": transcript.get("text") or "",
            "segments": transcript.get("segments") or [],
        }

    def mark_transcript_failed(self, asset_id: str, message: str) -> None:
        self.asset_repo.update_status(asset_id, "failed")
        self.asset_repo.update_transcript(
            asset_id=asset_id,
            status="failed",
            text=None,
            segments=None,
            error_code="E-1001",
            error_message=message,
            updated_at=now_iso(),
        )


class AssetService:
    def __init__(self, asset_repo: AssetRepository, session_repo: SessionRepository, transcript_service: TranscriptService):
        self.asset_repo = asset_repo
        self.session_repo = session_repo
        self.transcript_service = transcript_service

    def create_text_asset(self, session_id: str, asset_type: str, content: str) -> dict[str, Any]:
        if asset_type not in {"food_name", "scenic_name", "text"}:
            raise DomainError(code="E-1099", message="素材类型不支持", status_code=400)
        if not self.session_repo.get(session_id):
            raise not_found("会话", session_id)
        asset = {
            "id": new_id(),
            "session_id": session_id,
            "asset_type": asset_type,
            "content": content,
            "file_path": None,
            "status": "ready",
            "created_at": now_iso(),
        }
        return self.asset_repo.create(asset)

    def ensure_video_upload_ready(self, session_id: str) -> None:
        self.transcript_service.ensure_video_upload_ready(session_id)

    def create_video_asset(self, session_id: str, file_path: str, file_name: str) -> dict[str, Any]:
        return self.transcript_service.create_video_asset(session_id, file_path, file_name)

    def create_image_asset(self, session_id: str, file_path: str, file_name: str) -> dict[str, Any]:
        if not self.session_repo.get(session_id):
            raise not_found("会话", session_id)
        asset = {
            "id": new_id(),
            "session_id": session_id,
            "asset_type": "image",
            "content": file_name,
            "file_path": file_path,
            "status": "ready",
            "created_at": now_iso(),
        }
        return self.asset_repo.create(asset)

    def get_transcript(self, asset_id: str) -> dict[str, Any]:
        return self.transcript_service.get_transcript(asset_id)
