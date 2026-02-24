from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.api.deps import get_generation_service
from backend.app.schemas.request import GenerationJobCreateRequest
from backend.app.schemas.response import (
    GenerationJob,
    GenerationResult,
    GenerationStageListResponse,
    JobAssetBreakdownResponse,
)
from backend.app.services.generation_service import GenerationService

router = APIRouter(tags=["Generation"])


@router.post("/jobs/generate", response_model=GenerationJob, status_code=status.HTTP_202_ACCEPTED)
def create_generation_job(
    payload: GenerationJobCreateRequest,
    service: GenerationService = Depends(get_generation_service),
) -> dict:
    return service.create_job(payload.session_id, payload.style_profile_id, payload.image_count)


@router.get("/jobs/{job_id}", response_model=GenerationJob)
def get_generation_job(job_id: str, service: GenerationService = Depends(get_generation_service)) -> dict:
    return service.get_job(job_id)


@router.get("/jobs/{job_id}/results", response_model=GenerationResult)
def get_generation_result(job_id: str, service: GenerationService = Depends(get_generation_service)) -> dict:
    return service.get_result(job_id)


@router.get("/jobs/{job_id}/stages", response_model=GenerationStageListResponse)
def get_generation_job_stages(job_id: str, service: GenerationService = Depends(get_generation_service)) -> dict:
    return service.get_stages(job_id)


@router.get("/jobs/{job_id}/asset-breakdown", response_model=JobAssetBreakdownResponse)
def get_generation_job_asset_breakdown(
    job_id: str,
    service: GenerationService = Depends(get_generation_service),
) -> dict:
    return service.get_asset_breakdown(job_id)


@router.post("/jobs/{job_id}/cancel", response_model=GenerationJob, status_code=status.HTTP_202_ACCEPTED)
def cancel_generation_job(job_id: str, service: GenerationService = Depends(get_generation_service)) -> dict:
    return service.cancel(job_id)
