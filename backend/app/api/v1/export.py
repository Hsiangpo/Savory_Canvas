from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.api.deps import get_export_service
from backend.app.schemas.request import ExportTaskCreateRequest
from backend.app.schemas.response import ExportTask
from backend.app.services.export_service import ExportService

router = APIRouter(tags=["Export"])


@router.post("/exports", response_model=ExportTask, status_code=status.HTTP_202_ACCEPTED)
def create_export_task(
    payload: ExportTaskCreateRequest,
    service: ExportService = Depends(get_export_service),
) -> dict:
    return service.create_task(payload.session_id, payload.job_id, payload.export_format)


@router.get("/exports/{export_id}", response_model=ExportTask)
def get_export_task(export_id: str, service: ExportService = Depends(get_export_service)) -> dict:
    return service.get_task(export_id)
