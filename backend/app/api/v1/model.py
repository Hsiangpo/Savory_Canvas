from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.app.api.deps import get_model_service
from backend.app.schemas.request import ModelRoutingUpdateRequest
from backend.app.schemas.response import ModelListResponse, ModelRoutingConfig
from backend.app.services.model_service import ModelService

router = APIRouter(tags=["Model"])


@router.get("/models", response_model=ModelListResponse)
def list_models(provider_id: str, service: ModelService = Depends(get_model_service)) -> dict:
    return service.list_models(provider_id)


@router.get("/config/model-routing", response_model=ModelRoutingConfig | None)
def get_model_routing(service: ModelService = Depends(get_model_service)) -> dict | None:
    return service.get_routing()


@router.post("/config/model-routing", response_model=ModelRoutingConfig)
def update_model_routing(
    payload: ModelRoutingUpdateRequest,
    service: ModelService = Depends(get_model_service),
) -> dict:
    return service.update_routing(payload.model_dump())
