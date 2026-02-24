from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.api.deps import get_provider_service
from backend.app.schemas.request import ProviderCreateRequest, ProviderUpdateRequest
from backend.app.schemas.response import DeleteResponse, Provider, ProviderListResponse
from backend.app.services.provider_service import ProviderService

router = APIRouter(tags=["Provider"])


@router.get("/providers", response_model=ProviderListResponse)
def list_providers(service: ProviderService = Depends(get_provider_service)) -> dict:
    return {"items": service.list_all()}


@router.post("/providers", response_model=Provider, status_code=status.HTTP_201_CREATED)
def create_provider(
    payload: ProviderCreateRequest,
    service: ProviderService = Depends(get_provider_service),
) -> dict:
    return service.create(
        name=payload.name,
        base_url=payload.base_url,
        api_key=payload.api_key,
        api_protocol=payload.api_protocol,
    )


@router.patch("/providers/{provider_id}", response_model=Provider)
def update_provider(
    provider_id: str,
    payload: ProviderUpdateRequest,
    service: ProviderService = Depends(get_provider_service),
) -> dict:
    return service.update(provider_id, payload.model_dump(exclude_none=True))


@router.delete("/providers/{provider_id}", response_model=DeleteResponse)
def delete_provider(provider_id: str, service: ProviderService = Depends(get_provider_service)) -> dict:
    return {"deleted": service.delete(provider_id)}
