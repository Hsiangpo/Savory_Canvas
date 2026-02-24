from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile

from backend.app.api.deps import get_inspiration_service
from backend.app.schemas.response import InspirationConversationResponse
from backend.app.services.inspiration_service import InspirationService

router = APIRouter(tags=["Inspiration"])


@router.get("/inspirations/{session_id}", response_model=InspirationConversationResponse)
def get_inspiration_conversation(
    session_id: str,
    service: InspirationService = Depends(get_inspiration_service),
) -> dict:
    return service.get_conversation(session_id)


@router.post("/inspirations/messages", response_model=InspirationConversationResponse)
async def post_inspiration_message(
    session_id: str = Form(...),
    text: str | None = Form(default=None),
    selected_items: list[str] = Form(default=[]),
    action: Literal[
        "continue",
        "confirm_prompt",
        "confirm_assets",
        "revise_assets",
        "use_style_profile",
        "save_style",
        "skip_save",
    ] | None = Form(default=None),
    image_usages: list[Literal["style_reference", "content_asset"]] = Form(default=[]),
    images: list[UploadFile] = File(default=[]),
    videos: list[UploadFile] = File(default=[]),
    service: InspirationService = Depends(get_inspiration_service),
) -> dict:
    return await service.send_message(
        session_id=session_id,
        text=text,
        selected_items=selected_items,
        action=action,
        image_usages=image_usages,
        images=images,
        videos=videos,
    )
