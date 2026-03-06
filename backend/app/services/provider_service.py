from __future__ import annotations

from backend.app.core.secrets import decrypt_text, encrypt_text
from backend.app.core.errors import DomainError, not_found
from backend.app.core.utils import new_id, now_iso
from backend.app.repositories.provider_repo import ProviderRepository


def mask_api_key(api_key: str) -> str:
    if len(api_key) <= 4:
        return "*" * len(api_key)
    return f"{'*' * (len(api_key) - 4)}{api_key[-4:]}"


class ProviderService:
    def __init__(self, provider_repo: ProviderRepository):
        self.provider_repo = provider_repo

    def list_all(self) -> list[dict]:
        providers = self.provider_repo.list_all()
        for provider in providers:
            provider.pop("api_key", None)
        return providers

    def create(self, name: str, base_url: str, api_key: str, api_protocol: str) -> dict:
        if api_protocol not in {"responses", "chat_completions"}:
            raise DomainError(code="E-1099", message="不支持的协议类型", status_code=400)
        now = now_iso()
        provider = {
            "id": new_id(),
            "name": name,
            "base_url": base_url,
            "api_key": encrypt_text(api_key),
            "api_key_masked": mask_api_key(api_key),
            "api_protocol": api_protocol,
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        }
        saved = self.provider_repo.create(provider)
        saved.pop("api_key", None)
        return saved

    def update(self, provider_id: str, payload: dict) -> dict:
        current = self.provider_repo.get(provider_id)
        if not current:
            raise not_found("提供商", provider_id)

        merged = {}
        for key in ("name", "base_url", "api_protocol", "enabled"):
            if key in payload and payload[key] is not None:
                merged[key] = payload[key]

        if "api_key" in payload and payload["api_key"]:
            merged["api_key"] = encrypt_text(payload["api_key"])
            merged["api_key_masked"] = mask_api_key(payload["api_key"])

        updated = self.provider_repo.update(provider_id, merged, now_iso())
        if not updated:
            raise not_found("提供商", provider_id)
        updated.pop("api_key", None)
        return updated

    def delete(self, provider_id: str) -> bool:
        deleted = self.provider_repo.delete(provider_id)
        if not deleted:
            raise not_found("提供商", provider_id)
        return True

    def require_provider(self, provider_id: str) -> dict:
        provider = self.provider_repo.get(provider_id)
        if not provider:
            raise not_found("提供商", provider_id)
        return provider
