from __future__ import annotations

from typing import Any

from backend.app.domain.enums import FINAL_JOB_STATUSES
from backend.app.infra.db import Database


class JobRepository:
    def __init__(self, db: Database):
        self.db = db

    def create_with_initial_log(self, job: dict[str, Any], log_id: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO generation_job (
                    id, session_id, style_profile_id, image_count,
                    status, progress_percent, current_stage, stage_message,
                    error_code, error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["id"],
                    job["session_id"],
                    job["style_profile_id"],
                    job["image_count"],
                    job["status"],
                    job["progress_percent"],
                    job["current_stage"],
                    job["stage_message"],
                    job.get("error_code"),
                    job.get("error_message"),
                    job["created_at"],
                    job["updated_at"],
                ),
            )
            conn.execute(
                """
                INSERT INTO job_stage_log (id, job_id, stage, stage_message, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    log_id,
                    job["id"],
                    job["current_stage"],
                    job["stage_message"],
                    job["status"],
                    job["created_at"],
                ),
            )
        return job

    def update_state_with_log(
        self,
        *,
        job_id: str,
        status: str,
        log_status: str | None = None,
        progress_percent: int,
        current_stage: str,
        stage_message: str,
        error_code: str | None,
        error_message: str | None,
        updated_at: str,
        log_id: str,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE generation_job
                SET status = ?, progress_percent = ?, current_stage = ?, stage_message = ?,
                    error_code = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    progress_percent,
                    current_stage,
                    stage_message,
                    error_code,
                    error_message,
                    updated_at,
                    job_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO job_stage_log (id, job_id, stage, stage_message, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (log_id, job_id, current_stage, stage_message, log_status or status, updated_at),
            )

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self.db.fetch_one(
            """
            SELECT id, session_id, style_profile_id, image_count,
                   status, progress_percent, current_stage, stage_message,
                   error_code, error_message, created_at, updated_at
            FROM generation_job
            WHERE id = ?
            """,
            (job_id,),
        )

    def list_by_session(self, session_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT id, session_id, style_profile_id, image_count,
                   status, progress_percent, current_stage, stage_message,
                   error_code, error_message, created_at, updated_at
            FROM generation_job
            WHERE session_id = ?
            ORDER BY created_at DESC
            """,
            (session_id,),
        )

    def cancel(self, job_id: str, updated_at: str, log_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if not job:
            return None
        if job["status"] in FINAL_JOB_STATUSES:
            return job
        self.update_state_with_log(
            job_id=job_id,
            status="canceled",
            log_status="canceled",
            progress_percent=job.get("progress_percent", 0),
            current_stage=job.get("current_stage") or "finalize",
            stage_message="任务已取消",
            error_code=job.get("error_code"),
            error_message=job.get("error_message"),
            updated_at=updated_at,
            log_id=log_id,
        )
        return self.get(job_id)

    def list_stage_logs(self, job_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT stage, status, stage_message, created_at
            FROM job_stage_log
            WHERE job_id = ?
            ORDER BY created_at ASC
            """,
            (job_id,),
        )
