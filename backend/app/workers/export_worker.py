from __future__ import annotations

import asyncio
import threading

from backend.app.core.utils import now_iso
from backend.app.repositories.export_repo import ExportRepository
from backend.app.repositories.job_repo import JobRepository
from backend.app.repositories.result_repo import ResultRepository
from backend.app.infra.storage import Storage


class ExportWorker:
    def __init__(
        self,
        export_repo: ExportRepository,
        job_repo: JobRepository,
        result_repo: ResultRepository,
        storage: Storage,
    ):
        self.export_repo = export_repo
        self.job_repo = job_repo
        self.result_repo = result_repo
        self.storage = storage

    def schedule(self, export_id: str) -> None:
        threading.Thread(target=lambda: asyncio.run(self._run(export_id)), daemon=True).start()

    async def _run(self, export_id: str) -> None:
        task = self.export_repo.get(export_id)
        if not task:
            return

        try:
            self.export_repo.update_state(
                export_id,
                status="running",
                file_path=None,
                error_code=None,
                error_message=None,
            )
            await asyncio.sleep(0.08)

            job = self.job_repo.get(task["job_id"])
            if not job or job["status"] not in {"success", "partial_success"}:
                self._fail(export_id, "生成任务尚未完成")
                return

            images = self.result_repo.list_images(task["job_id"])
            copy_result = self.result_repo.get_copy(task["job_id"])
            if not images or not copy_result:
                self._fail(export_id, "缺少可导出内容")
                return

            extension = "pdf" if task["export_format"] == "pdf" else "txt"
            file_path = self.storage.save_export(
                filename=f"{export_id}.{extension}",
                content=copy_result["full_text"],
            )
            self.export_repo.update_state(
                export_id,
                status="success",
                file_path=file_path,
                error_code=None,
                error_message=None,
            )
        except Exception:
            self._fail(export_id, "导出流程异常")

    def _fail(self, export_id: str, message: str) -> None:
        self.export_repo.update_state(
            export_id,
            status="failed",
            file_path=None,
            error_code="E-1005",
            error_message=message,
        )

