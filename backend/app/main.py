from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.router import api_router
from backend.app.core.errors import DomainError
from backend.app.core.settings import load_settings
from backend.app.infra.db import Database
from backend.app.infra.storage import Storage
from backend.app.repositories.asset_repo import AssetRepository
from backend.app.repositories.config_repo import ConfigRepository
from backend.app.repositories.export_repo import ExportRepository
from backend.app.repositories.inspiration_repo import InspirationRepository
from backend.app.repositories.job_repo import JobRepository
from backend.app.repositories.provider_repo import ProviderRepository
from backend.app.repositories.result_repo import ResultRepository
from backend.app.repositories.session_repo import SessionRepository
from backend.app.repositories.style_repo import StyleRepository
from backend.app.services.asset_service import AssetService, TranscriptService
from backend.app.services.container import ServiceContainer
from backend.app.services.export_service import ExportService
from backend.app.services.generation_service import GenerationService
from backend.app.services.inspiration_service import InspirationService
from backend.app.services.model_service import ModelService
from backend.app.services.provider_service import ProviderService
from backend.app.services.session_service import SessionService
from backend.app.services.style_service import StyleService
from backend.app.workers.export_worker import ExportWorker
from backend.app.workers.generation_worker import GenerationWorker
from backend.app.workers.transcript_worker import TranscriptWorker


def create_app() -> FastAPI:
    settings = load_settings()

    database = Database(settings.db_path)
    migration_path = Path(__file__).resolve().parents[1] / "migrations" / "001_init.sql"
    database.initialize(migration_path)

    storage = Storage(settings.storage_dir)

    session_repo = SessionRepository(database)
    asset_repo = AssetRepository(database)
    style_repo = StyleRepository(database)
    job_repo = JobRepository(database)
    result_repo = ResultRepository(database)
    export_repo = ExportRepository(database)
    provider_repo = ProviderRepository(database)
    config_repo = ConfigRepository(database)
    inspiration_repo = InspirationRepository(database)

    model_service = ModelService(config_repo=config_repo, provider_repo=provider_repo)
    provider_service = ProviderService(provider_repo=provider_repo)

    transcript_worker = TranscriptWorker(asset_repo=asset_repo)
    generation_worker = GenerationWorker(
        job_repo=job_repo,
        asset_repo=asset_repo,
        inspiration_repo=inspiration_repo,
        style_repo=style_repo,
        result_repo=result_repo,
        session_repo=session_repo,
        model_service=model_service,
        storage=storage,
    )
    export_worker = ExportWorker(
        export_repo=export_repo,
        job_repo=job_repo,
        result_repo=result_repo,
        storage=storage,
    )

    transcript_service = TranscriptService(
        asset_repo=asset_repo,
        session_repo=session_repo,
        worker=transcript_worker,
    )
    session_service = SessionService(
        session_repo=session_repo,
        asset_repo=asset_repo,
        job_repo=job_repo,
        export_repo=export_repo,
    )
    asset_service = AssetService(
        asset_repo=asset_repo,
        session_repo=session_repo,
        transcript_service=transcript_service,
    )
    style_service = StyleService(
        style_repo=style_repo,
        session_repo=session_repo,
        model_service=model_service,
        asset_repo=asset_repo,
        storage=storage,
        public_base_url=settings.public_base_url,
    )
    inspiration_service = InspirationService(
        inspiration_repo=inspiration_repo,
        session_repo=session_repo,
        asset_repo=asset_repo,
        style_repo=style_repo,
        asset_service=asset_service,
        transcript_service=transcript_service,
        style_service=style_service,
        model_service=model_service,
        storage=storage,
    )
    generation_service = GenerationService(
        job_repo=job_repo,
        result_repo=result_repo,
        asset_repo=asset_repo,
        session_repo=session_repo,
        style_repo=style_repo,
        worker=generation_worker,
        storage=storage,
        public_base_url=settings.public_base_url,
    )
    export_service = ExportService(
        export_repo=export_repo,
        session_repo=session_repo,
        job_repo=job_repo,
        worker=export_worker,
    )

    app = FastAPI(
        title="Savory Canvas API",
        version="1.2.1",
        description="Savory Canvas 本地图文生成系统接口文档",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:7778", "http://127.0.0.1:7778"],
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )
    app.mount("/static", StaticFiles(directory=str(storage.base_dir)), name="static")
    app.include_router(api_router)

    app.state.storage = storage
    app.state.services = ServiceContainer(
        session=session_service,
        asset=asset_service,
        transcript=transcript_service,
        style=style_service,
        inspiration=inspiration_service,
        generation=generation_service,
        export=export_service,
        provider=provider_service,
        model=model_service,
    )

    @app.exception_handler(DomainError)
    async def handle_domain_error(_, exc: DomainError):
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "code": "E-1099",
                "message": "请求参数不合法",
                "details": {"errors": jsonable_encoder(exc.errors())},
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={
                "code": "E-1099",
                "message": "系统内部错误",
                "details": {"error": str(exc)},
            },
        )

    return app


app = create_app()
