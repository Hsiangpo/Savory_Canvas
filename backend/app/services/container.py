from __future__ import annotations

from dataclasses import dataclass

from backend.app.services.asset_service import AssetService, TranscriptService
from backend.app.services.export_service import ExportService
from backend.app.services.generation_service import GenerationService
from backend.app.services.inspiration_service import InspirationService
from backend.app.services.model_service import ModelService
from backend.app.services.provider_service import ProviderService
from backend.app.services.session_service import SessionService
from backend.app.services.style_service import StyleService


@dataclass
class ServiceContainer:
    session: SessionService
    asset: AssetService
    transcript: TranscriptService
    style: StyleService
    inspiration: InspirationService
    generation: GenerationService
    export: ExportService
    provider: ProviderService
    model: ModelService
