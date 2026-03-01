from __future__ import annotations

from pathlib import Path

from backend.app.core.errors import not_found
from backend.app.core.utils import new_id, now_iso
from backend.app.infra.storage import Storage
from backend.app.repositories.export_repo import ExportRepository
from backend.app.repositories.job_repo import JobRepository
from backend.app.repositories.session_repo import SessionRepository
from backend.app.workers.export_worker import ExportWorker


class ExportService:
    def __init__(
        self,
        export_repo: ExportRepository,
        session_repo: SessionRepository,
        job_repo: JobRepository,
        worker: ExportWorker,
        storage: Storage,
        public_base_url: str,
    ):
        self.export_repo = export_repo
        self.session_repo = session_repo
        self.job_repo = job_repo
        self.worker = worker
        self.storage = storage
        self.public_base_url = public_base_url.rstrip("/")

    def create_task(self, session_id: str, job_id: str, export_format: str) -> dict:
        if not self.session_repo.get(session_id):
            raise not_found("会话", session_id)
        if not self.job_repo.get(job_id):
            raise not_found("任务", job_id)

        task = {
            "id": new_id(),
            "session_id": session_id,
            "job_id": job_id,
            "export_format": export_format,
            "status": "queued",
            "file_path": None,
            "error_code": None,
            "error_message": None,
            "created_at": now_iso(),
        }
        self.export_repo.create(task)
        self.worker.schedule(task["id"])
        return self._with_public_file_url(self.export_repo.get(task["id"]) or task)

    def get_task(self, export_id: str) -> dict:
        task = self.export_repo.get(export_id)
        if not task:
            raise not_found("导出任务", export_id)
        return self._with_public_file_url(task)

    def _with_public_file_url(self, task: dict) -> dict:
        file_url = task.get("file_url")
        if not isinstance(file_url, str) or not file_url.strip():
            return task
        normalized = file_url.replace("\\", "/").strip()
        if normalized.startswith("http://") or normalized.startswith("https://"):
            return task
        static_relative = normalized.lstrip("/")
        if static_relative.startswith("static/"):
            static_relative = static_relative[len("static/") :]
        else:
            try:
                static_relative = Path(file_url).resolve().relative_to(self.storage.base_dir.resolve()).as_posix()
            except Exception:
                static_relative = static_relative
        task["file_url"] = f"{self.public_base_url}/static/{static_relative.lstrip('/')}"
        return task
