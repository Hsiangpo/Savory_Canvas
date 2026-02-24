from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.api.deps import get_style_service
from backend.app.schemas.request import (
    StyleChatRequest,
    StyleProfileCreateRequest,
    StyleProfileUpdateRequest,
)
from backend.app.schemas.response import DeleteResponse, StyleChatResponse, StyleProfile, StyleProfileListResponse
from backend.app.services.style_service import StyleService

router = APIRouter(tags=["Style"])


@router.post("/styles/chat", response_model=StyleChatResponse)
def style_chat(payload: StyleChatRequest, service: StyleService = Depends(get_style_service)) -> dict:
    return service.chat(
        session_id=payload.session_id,
        stage=payload.stage,
        user_reply=payload.user_reply,
        selected_items=payload.selected_items,
    )


@router.post("/styles", response_model=StyleProfile, status_code=status.HTTP_201_CREATED)
def create_style_profile(
    payload: StyleProfileCreateRequest,
    service: StyleService = Depends(get_style_service),
) -> dict:
    return service.create(payload.session_id, payload.name, payload.style_payload.model_dump())


@router.get("/styles", response_model=StyleProfileListResponse)
def list_style_profiles(service: StyleService = Depends(get_style_service)) -> dict:
    return {"items": service.list_all()}


@router.patch("/styles/{style_id}", response_model=StyleProfile)
def update_style_profile(
    style_id: str,
    payload: StyleProfileUpdateRequest,
    service: StyleService = Depends(get_style_service),
) -> dict:
    style_payload = payload.style_payload.model_dump() if payload.style_payload is not None else None
    return service.update(style_id, payload.name, style_payload)


@router.delete("/styles/{style_id}", response_model=DeleteResponse)
def delete_style_profile(style_id: str, service: StyleService = Depends(get_style_service)) -> dict:
    return {"deleted": service.delete(style_id)}
