from __future__ import annotations

from fastapi import Request

from backend.app.infra.storage import Storage
from backend.app.services.asset_service import AssetService, TranscriptService
from backend.app.services.export_service import ExportService
from backend.app.services.generation_service import GenerationService
from backend.app.services.inspiration_service import InspirationService
from backend.app.services.model_service import ModelService
from backend.app.services.provider_service import ProviderService
from backend.app.services.session_service import SessionService
from backend.app.services.style_service import StyleService


def _services(request: Request):
    return request.app.state.services


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_session_service(request: Request) -> SessionService:
    return _services(request).session


def get_asset_service(request: Request) -> AssetService:
    return _services(request).asset


def get_transcript_service(request: Request) -> TranscriptService:
    return _services(request).transcript


def get_style_service(request: Request) -> StyleService:
    return _services(request).style


def get_generation_service(request: Request) -> GenerationService:
    return _services(request).generation


def get_inspiration_service(request: Request) -> InspirationService:
    return _services(request).inspiration


def get_export_service(request: Request) -> ExportService:
    return _services(request).export


def get_provider_service(request: Request) -> ProviderService:
    return _services(request).provider


def get_model_service(request: Request) -> ModelService:
    return _services(request).model
