from __future__ import annotations

from dataclasses import dataclass

from langchain_openai import ChatOpenAI

from backend.app.core.errors import DomainError
from backend.app.services.model_service import ModelService


@dataclass
class AgentLLMProvider:
    model_service: ModelService
    temperature: float = 0.1
    timeout: float = 30.0

    def build_chat_model(self) -> ChatOpenAI:
        routing = self.model_service.require_routing()
        text_model = routing.get("text_model") or {}
        provider_id = str(text_model.get("provider_id") or "").strip()
        model_name = str(text_model.get("model_name") or "").strip()
        if not provider_id or not model_name:
            raise DomainError(code="E-1006", message="请先完成模型设置", status_code=400)

        provider = self.model_service.provider_repo.get(provider_id)
        if not provider or not provider.get("enabled"):
            raise DomainError(code="E-1006", message="文字模型提供商不可用", status_code=400)

        return ChatOpenAI(
            model=model_name,
            api_key=provider["api_key"],
            base_url=self._normalize_base_url(str(provider.get("base_url") or "")),
            temperature=self.temperature,
            timeout=self.timeout,
        )

    def _normalize_base_url(self, base_url: str) -> str:
        normalized = base_url.strip().rstrip("/")
        if normalized.lower().endswith("/v1"):
            return normalized
        return f"{normalized}/v1"
