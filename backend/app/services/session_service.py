from __future__ import annotations

from backend.app.core.errors import not_found
from backend.app.core.utils import new_id, now_iso
from backend.app.repositories.asset_repo import AssetRepository
from backend.app.repositories.export_repo import ExportRepository
from backend.app.repositories.job_repo import JobRepository
from backend.app.repositories.session_repo import SessionRepository


class SessionService:
    def __init__(
        self,
        session_repo: SessionRepository,
        asset_repo: AssetRepository,
        job_repo: JobRepository,
        export_repo: ExportRepository,
    ):
        self.session_repo = session_repo
        self.asset_repo = asset_repo
        self.job_repo = job_repo
        self.export_repo = export_repo

    def create_session(self, title: str, content_mode: str) -> dict:
        now = now_iso()
        session = {
            "id": new_id(),
            "title": title,
            "content_mode": content_mode,
            "created_at": now,
            "updated_at": now,
        }
        return self.session_repo.create(session)

    def list_sessions(self) -> list[dict]:
        return self.session_repo.list_all()

    def rename_session(self, session_id: str, title: str) -> dict:
        updated = self.session_repo.update_session_title(
            session_id=session_id,
            title=title,
            updated_at=now_iso(),
        )
        if not updated:
            raise not_found("会话", session_id)
        return updated

    def delete_session(self, session_id: str) -> bool:
        deleted = self.session_repo.delete_session(session_id)
        if not deleted:
            raise not_found("会话", session_id)
        return True

    def get_session_detail(self, session_id: str) -> dict:
        session = self.session_repo.get(session_id)
        if not session:
            raise not_found("会话", session_id)
        return {
            "session": session,
            "assets": self.asset_repo.list_by_session(session_id),
            "jobs": self.job_repo.list_by_session(session_id),
            "exports": self.export_repo.list_by_session(session_id),
        }

    def require_session(self, session_id: str) -> dict:
        session = self.session_repo.get(session_id)
        if not session:
            raise not_found("会话", session_id)
        return session
