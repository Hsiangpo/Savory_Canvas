from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.v1.asset import router as asset_router
from backend.app.api.v1.export import router as export_router
from backend.app.api.v1.generation import router as generation_router
from backend.app.api.v1.inspiration import router as inspiration_router
from backend.app.api.v1.model import router as model_router
from backend.app.api.v1.provider import router as provider_router
from backend.app.api.v1.session import router as session_router
from backend.app.api.v1.style import router as style_router


api_router = APIRouter(prefix="/api/v1")
api_router.include_router(session_router)
api_router.include_router(inspiration_router)
api_router.include_router(asset_router)
api_router.include_router(style_router)
api_router.include_router(generation_router)
api_router.include_router(export_router)
api_router.include_router(provider_router)
api_router.include_router(model_router)
