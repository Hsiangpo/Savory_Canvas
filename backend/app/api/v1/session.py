from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.api.deps import get_session_service
from backend.app.schemas.request import SessionCreateRequest, SessionUpdateRequest
from backend.app.schemas.response import DeleteResponse, Session, SessionDetailResponse, SessionListResponse
from backend.app.services.session_service import SessionService

router = APIRouter(tags=["Session"])


@router.post("/sessions", response_model=Session, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: SessionCreateRequest,
    service: SessionService = Depends(get_session_service),
) -> dict:
    return service.create_session(payload.title, payload.content_mode)


@router.get("/sessions", response_model=SessionListResponse)
def list_sessions(service: SessionService = Depends(get_session_service)) -> dict:
    return {"items": service.list_sessions()}


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
def get_session_detail(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> dict:
    return service.get_session_detail(session_id)


@router.patch("/sessions/{session_id}", response_model=Session)
def update_session(
    session_id: str,
    payload: SessionUpdateRequest,
    service: SessionService = Depends(get_session_service),
) -> dict:
    return service.rename_session(session_id, payload.title)


@router.delete("/sessions/{session_id}", response_model=DeleteResponse)
def delete_session(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> dict:
    return {"deleted": service.delete_session(session_id)}
