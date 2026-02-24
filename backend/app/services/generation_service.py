from __future__ import annotations

from pathlib import Path

from backend.app.core.errors import DomainError, not_found
from backend.app.core.utils import new_id, now_iso
from backend.app.infra.storage import Storage
from backend.app.repositories.asset_repo import AssetRepository
from backend.app.repositories.job_repo import JobRepository
from backend.app.repositories.result_repo import ResultRepository
from backend.app.repositories.session_repo import SessionRepository
from backend.app.repositories.style_repo import StyleRepository
from backend.app.workers.generation_worker import GenerationWorker


EMPTY_COPY = {
    "title": "",
    "intro": "",
    "guide_sections": [],
    "ending": "",
    "full_text": "",
}


class GenerationService:
    def __init__(
        self,
        job_repo: JobRepository,
        result_repo: ResultRepository,
        asset_repo: AssetRepository,
        session_repo: SessionRepository,
        style_repo: StyleRepository,
        worker: GenerationWorker,
        storage: Storage,
        public_base_url: str,
    ):
        self.job_repo = job_repo
        self.result_repo = result_repo
        self.asset_repo = asset_repo
        self.session_repo = session_repo
        self.style_repo = style_repo
        self.worker = worker
        self.storage = storage
        self.public_base_url = public_base_url.rstrip("/")

    def create_job(self, session_id: str, style_profile_id: str, image_count: int) -> dict:
        if image_count < 1 or image_count > 10:
            raise DomainError(code="E-1099", message="图片数量不合法", status_code=400)
        if not self.session_repo.get(session_id):
            raise not_found("会话", session_id)
        if not self.style_repo.get(style_profile_id):
            raise not_found("风格", style_profile_id)

        now = now_iso()
        job = {
            "id": new_id(),
            "session_id": session_id,
            "style_profile_id": style_profile_id,
            "image_count": image_count,
            "status": "queued",
            "progress_percent": 0,
            "current_stage": "asset_extract",
            "stage_message": "任务已创建",
            "error_code": None,
            "error_message": None,
            "created_at": now,
            "updated_at": now,
        }
        self.job_repo.create_with_initial_log(job, log_id=new_id())
        self.worker.schedule(job["id"])
        return job

    def get_job(self, job_id: str) -> dict:
        job = self.job_repo.get(job_id)
        if not job:
            raise not_found("任务", job_id)
        return job

    def get_result(self, job_id: str) -> dict:
        job = self.job_repo.get(job_id)
        if not job:
            raise not_found("任务", job_id)
        raw_images = self.result_repo.list_images(job_id)
        images = [
            {
                "image_index": item["image_index"],
                "asset_refs": item.get("asset_refs") or [],
                "prompt_text": item["prompt_text"],
                "image_url": self._build_image_url(item["image_path"]),
            }
            for item in raw_images
        ]
        copy_result = self.result_repo.get_copy(job_id) or EMPTY_COPY
        return {
            "job_id": job_id,
            "status": job["status"],
            "images": images,
            "copy": copy_result,
        }

    def get_stages(self, job_id: str) -> dict:
        job = self.job_repo.get(job_id)
        if not job:
            raise not_found("任务", job_id)
        return {
            "job_id": job_id,
            "items": self.job_repo.list_stage_logs(job_id),
        }

    def get_asset_breakdown(self, job_id: str) -> dict:
        job = self.job_repo.get(job_id)
        if not job:
            raise not_found("任务", job_id)
        breakdown = self.result_repo.get_asset_breakdown(job_id)
        if breakdown:
            return breakdown
        return self._build_breakdown_from_session_assets(job)

    def cancel(self, job_id: str) -> dict:
        canceled = self.job_repo.cancel(job_id, updated_at=now_iso(), log_id=new_id())
        if not canceled:
            raise not_found("任务", job_id)
        return canceled

    def _build_image_url(self, image_path: str) -> str:
        normalized_path = image_path.replace("\\", "/")
        if normalized_path.startswith("http://") or normalized_path.startswith("https://"):
            return normalized_path

        static_relative = normalized_path.lstrip("/")
        if static_relative.startswith("static/"):
            static_relative = static_relative[len("static/") :]
        elif static_relative.startswith("generated/"):
            pass
        else:
            try:
                resolved = Path(image_path).resolve()
                static_relative = resolved.relative_to(self.storage.base_dir.resolve()).as_posix()
            except Exception:
                static_relative = static_relative
        return f"{self.public_base_url}/static/{static_relative.lstrip('/')}"

    def _build_breakdown_from_session_assets(self, job: dict) -> dict:
        session = self.session_repo.get(job["session_id"]) or {}
        assets = self.asset_repo.list_by_session(job["session_id"])
        source_assets = [
            {
                "asset_id": asset["id"],
                "asset_type": asset["asset_type"],
                "content": asset.get("content"),
            }
            for asset in assets
        ]
        foods: list[str] = []
        scenes: list[str] = []
        keywords: list[str] = []
        for source in source_assets:
            text = (source.get("content") or "").strip()
            if not text:
                continue
            keywords.append(text)
            if source["asset_type"] == "food_name":
                foods.append(text)
            if source["asset_type"] == "scenic_name":
                scenes.append(text)
        if not foods:
            foods = keywords[:2]
        if not scenes:
            scenes = keywords[:2]
        return {
            "job_id": job["id"],
            "session_id": job["session_id"],
            "content_mode": session.get("content_mode") or "food",
            "source_assets": source_assets,
            "extracted": {"foods": foods, "scenes": scenes, "keywords": keywords[:15]},
            "created_at": job["created_at"],
        }
