from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SessionCreateRequest(BaseModel):
    title: str
    content_mode: Literal["food", "scenic", "food_scenic"]


class SessionUpdateRequest(BaseModel):
    title: str
    content_mode: Literal["food", "scenic", "food_scenic"] | None = None


class AssetTextCreateRequest(BaseModel):
    session_id: str
    asset_type: Literal["food_name", "scenic_name", "text"]
    content: str


class StyleChatRequest(BaseModel):
    session_id: str
    stage: Literal["init", "painting_style", "background_decor", "color_mood", "image_count"]
    user_reply: str
    selected_items: list[str] = Field(default_factory=list)

    @field_validator("stage", mode="before")
    @classmethod
    def normalize_stage(cls, value: Any) -> Any:
        if value == "init":
            return "painting_style"
        return value


class StylePayloadRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    painting_style: str
    color_mood: str
    prompt_example: str
    style_prompt: str
    sample_image_asset_id: str | None = None
    sample_image_asset_ids: list[str] = Field(default_factory=list)
    extra_keywords: list[str] = Field(default_factory=list)

    @field_validator("painting_style", "color_mood", "prompt_example", "style_prompt", mode="before")
    @classmethod
    def normalize_text_field(cls, value: Any) -> Any:
        if isinstance(value, list):
            merged = "、".join(str(item).strip() for item in value if str(item).strip())
            return merged
        return value


class StyleProfileCreateRequest(BaseModel):
    session_id: str | None = None
    name: str
    style_payload: StylePayloadRequest


class StyleProfileUpdateRequest(BaseModel):
    name: str | None = None
    style_payload: StylePayloadRequest | None = None

    @model_validator(mode="after")
    def validate_non_empty_patch(self) -> "StyleProfileUpdateRequest":
        if self.name is None and self.style_payload is None:
            raise ValueError("name 或 style_payload 至少提供一个")
        return self


class GenerationJobCreateRequest(BaseModel):
    session_id: str
    style_profile_id: str
    image_count: int = Field(ge=1, le=10)


class ExportTaskCreateRequest(BaseModel):
    session_id: str
    job_id: str
    export_format: Literal["long_image", "pdf"]


class ProviderCreateRequest(BaseModel):
    name: str
    base_url: str
    api_key: str
    api_protocol: Literal["responses", "chat_completions"]


class ProviderUpdateRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    api_protocol: Literal["responses", "chat_completions"] | None = None
    enabled: bool | None = None


class ModelReference(BaseModel):
    provider_id: str
    model_name: str


class ModelRoutingUpdateRequest(BaseModel):
    image_model: ModelReference
    text_model: ModelReference
