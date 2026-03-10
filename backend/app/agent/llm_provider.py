from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from backend.app.core.errors import DomainError
from backend.app.services.model_service import ModelService
from backend.app.services.style.errors import StyleFallbackError
from backend.app.services.style.model_caller_mixin import StyleModelCallerMixin
from backend.app.services.style.protocol_adapter_mixin import StyleProtocolAdapterMixin


@dataclass
class _AgentChatResponse:
    content: str
    reasoning_summaries: list[str] = field(default_factory=list)


class _AgentProtocolCaller(StyleModelCallerMixin, StyleProtocolAdapterMixin):
    _MAX_RETRY_ATTEMPTS = 3

    def __init__(self, *, temperature: float, timeout: float):
        self.temperature = temperature
        self.timeout = timeout
        self._provider_protocol_overrides: dict[str, str] = {}
        self.storage = None
        self.public_base_url = ""

    def call_text(
        self,
        *,
        provider: dict[str, Any],
        model_name: str,
        system_prompt: str,
        user_prompt: str,
    ) -> _AgentChatResponse:
        last_error: StyleFallbackError | None = None
        for attempt in range(self._MAX_RETRY_ATTEMPTS):
            try:
                return self._call_text_once(
                    provider=provider,
                    model_name=model_name,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
            except StyleFallbackError as exc:
                last_error = exc
                if attempt == self._MAX_RETRY_ATTEMPTS - 1 or not self._is_retryable_agent_error(exc):
                    break
        if last_error:
            raise last_error
        raise StyleFallbackError("protocol_both_failed", "双协议请求失败")

    def _call_text_once(
        self,
        *,
        provider: dict[str, Any],
        model_name: str,
        system_prompt: str,
        user_prompt: str,
    ) -> _AgentChatResponse:
        provider_id = str(provider.get("id") or "").strip()
        model_candidates = self._build_model_name_candidates(model_name)
        last_error: StyleFallbackError | None = None
        for model_candidate in model_candidates:
            protocol_order = self._build_protocol_order(provider_id, provider.get("api_protocol"))
            for index, protocol in enumerate(protocol_order):
                endpoint, request_body = self._build_protocol_request(
                    provider=provider,
                    protocol=protocol,
                    model_name=model_candidate,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    strict_json=False,
                )
                request_body["temperature"] = self.temperature
                try:
                    response_payload = self._post_json(
                        endpoint,
                        self._inject_reasoning_summary_request(request_body, protocol),
                        provider["api_key"],
                    )
                except StyleFallbackError as exc:
                    if protocol == "responses" and self._should_retry_without_reasoning_summary(exc):
                        response_payload = self._post_json(endpoint, request_body, provider["api_key"])
                    else:
                        last_error = exc
                        if index == 0 and self._should_retry_protocol(exc):
                            continue
                        break
                try:
                    text = self._extract_text_content(response_payload, protocol)
                    if not text.strip():
                        raise StyleFallbackError("upstream_empty_text", "上游未返回有效文本")
                    if provider_id:
                        self._provider_protocol_overrides[provider_id] = protocol
                    return _AgentChatResponse(
                        content=text,
                        reasoning_summaries=self._extract_reasoning_summaries(response_payload, protocol),
                    )
                except StyleFallbackError as exc:
                    last_error = exc
                    if index == 0 and self._should_retry_protocol(exc):
                        continue
                    break
            if self._should_retry_model_name(last_error) and model_candidate != model_candidates[-1]:
                continue
            break
        if last_error:
            raise last_error
        raise StyleFallbackError("protocol_both_failed", "双协议请求失败")

    def _is_retryable_agent_error(self, error: StyleFallbackError) -> bool:
        if error.reason == "upstream_timeout_or_network":
            return True
        if error.reason == "upstream_http_error":
            lowered = str(error.detail or "").lower()
            return any(marker in lowered for marker in ("500", "502", "503", "504", "gateway", "upstream"))
        if error.reason == "protocol_both_failed":
            lowered = str(error.detail or "").lower()
            return any(marker in lowered for marker in ("upstream_timeout_or_network", "500", "502", "503", "504", "gateway"))
        return False

    def to_user_message(self, error: StyleFallbackError) -> str:
        return self.build_user_facing_upstream_message(error, include_detail=False)

    def _inject_reasoning_summary_request(self, request_body: dict[str, Any], protocol: str) -> dict[str, Any]:
        if protocol != "responses":
            return request_body
        payload = dict(request_body)
        reasoning = payload.get("reasoning")
        normalized_reasoning = dict(reasoning) if isinstance(reasoning, dict) else {}
        normalized_reasoning["summary"] = "auto"
        payload["reasoning"] = normalized_reasoning
        return payload

    def _should_retry_without_reasoning_summary(self, error: StyleFallbackError) -> bool:
        if error.reason not in {"protocol_incompatible", "upstream_http_error"}:
            return False
        detail = str(error.detail or "").lower()
        return any(
            marker in detail
            for marker in (
                "reasoning",
                "summary",
                "summaries",
                "verification",
                "unsupported_value",
                "invalid_value",
                "invalid_request_error",
            )
        )

    def _extract_reasoning_summaries(self, payload: dict[str, Any], protocol: str) -> list[str]:
        if protocol != "responses":
            return []
        output_items = payload.get("output")
        if not isinstance(output_items, list):
            return []
        summaries: list[str] = []
        seen: set[str] = set()
        for item in output_items:
            if not isinstance(item, dict) or item.get("type") != "reasoning":
                continue
            raw_summary = item.get("summary")
            if isinstance(raw_summary, str):
                text = raw_summary.strip()
                if text and text not in seen:
                    seen.add(text)
                    summaries.append(text)
                continue
            if not isinstance(raw_summary, list):
                continue
            for part in raw_summary:
                if not isinstance(part, dict):
                    continue
                text = str(part.get("text") or "").strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                summaries.append(text)
        return summaries


@dataclass
class _AgentProtocolChatModel:
    protocol_caller: _AgentProtocolCaller
    provider: dict[str, Any]
    model_name: str

    def invoke(self, messages: list[BaseMessage]) -> _AgentChatResponse:
        system_prompt, user_prompt = self._flatten_messages(messages)
        try:
            response = self.protocol_caller.call_text(
                provider=self.provider,
                model_name=self.model_name,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        except StyleFallbackError as exc:
            raise DomainError(
                code="E-1099",
                message=self.protocol_caller.to_user_message(exc),
                status_code=503,
                details={"reason": exc.reason},
            ) from exc
        return response

    def _flatten_messages(self, messages: list[BaseMessage]) -> tuple[str, str]:
        system_parts: list[str] = []
        dialogue_parts: list[str] = []
        for message in messages:
            text = self._stringify_message_content(message)
            if not text:
                continue
            if isinstance(message, SystemMessage):
                system_parts.append(text)
            elif isinstance(message, HumanMessage):
                dialogue_parts.append(f"用户:\n{text}")
            elif isinstance(message, AIMessage):
                dialogue_parts.append(f"助手:\n{text}")
            else:
                dialogue_parts.append(f"消息:\n{text}")
        return "\n\n".join(system_parts), "\n\n".join(dialogue_parts)

    def _stringify_message_content(self, message: BaseMessage) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    text = item.strip()
                    if text:
                        parts.append(text)
                    continue
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            return "\n".join(parts).strip()
        return str(content or "").strip()


@dataclass
class AgentLLMProvider:
    model_service: ModelService
    temperature: float = 0.1
    timeout: float = 30.0

    def build_chat_model(self) -> _AgentProtocolChatModel:
        routing = self.model_service.require_routing()
        text_model = routing.get("text_model") or {}
        provider_id = str(text_model.get("provider_id") or "").strip()
        model_name = str(text_model.get("model_name") or "").strip()
        if not provider_id or not model_name:
            raise DomainError(code="E-1006", message="请先完成模型设置", status_code=400)

        provider = self.model_service.provider_repo.get(provider_id)
        if not provider or not provider.get("enabled"):
            raise DomainError(code="E-1006", message="文字模型提供商不可用", status_code=400)

        return _AgentProtocolChatModel(
            protocol_caller=_AgentProtocolCaller(temperature=self.temperature, timeout=self.timeout),
            provider=provider,
            model_name=model_name,
        )
