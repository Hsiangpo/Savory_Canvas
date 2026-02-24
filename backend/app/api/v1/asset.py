from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile, status

from backend.app.api.deps import get_asset_service, get_storage
from backend.app.infra.storage import Storage
from backend.app.schemas.request import AssetTextCreateRequest
from backend.app.schemas.response import Asset, TranscriptResult
from backend.app.services.asset_service import AssetService

router = APIRouter(tags=["Asset"])


@router.post("/assets/text", response_model=Asset, status_code=status.HTTP_201_CREATED)
def create_text_asset(
    payload: AssetTextCreateRequest,
    service: AssetService = Depends(get_asset_service),
) -> dict:
    return service.create_text_asset(payload.session_id, payload.asset_type, payload.content)


@router.post("/assets/video", response_model=Asset, status_code=status.HTTP_201_CREATED)
async def create_video_asset(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    service: AssetService = Depends(get_asset_service),
    storage: Storage = Depends(get_storage),
) -> dict:
    source_name = file.filename or "upload.mp4"
    source_path = Path(source_name)
    stem = source_path.stem or "upload"
    extension = source_path.suffix or ".mp4"
    file_name = f"{session_id}_{stem}{extension}"
    content = await file.read()
    file_path = storage.save_video(file_name, content)
    return service.create_video_asset(session_id=session_id, file_path=file_path, file_name=source_name)


@router.get("/assets/{asset_id}/transcript", response_model=TranscriptResult)
def get_transcript(asset_id: str, service: AssetService = Depends(get_asset_service)) -> dict:
    return service.get_transcript(asset_id)
