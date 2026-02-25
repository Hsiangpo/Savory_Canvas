from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    id: str
    title: str
    content_mode: str
    created_at: str
    updated_at: str


class SessionListResponse(BaseModel):
    items: list[Session]


class Asset(BaseModel):
    id: str
    session_id: str
    asset_type: Literal["food_name", "scenic_name", "text", "video", "transcript", "image"]
    content: str | None = None
    file_path: str | None = None
    status: Literal["ready", "processing", "failed"]
    created_at: str


class TranscriptResult(BaseModel):
    asset_id: str
    status: Literal["processing", "ready", "failed"]
    text: str | None = None
    segments: list[dict[str, Any]] = Field(default_factory=list)


class StyleOptionBlock(BaseModel):
    title: str
    items: list[str]
    max: int


class StyleChatResponse(BaseModel):
    reply: str
    options: StyleOptionBlock
    stage: str
    next_stage: str
    is_finished: bool
    fallback_used: bool


class StylePayload(BaseModel):
    painting_style: str
    color_mood: str
    prompt_example: str
    style_prompt: str
    sample_image_asset_id: str | None = None
    extra_keywords: list[str] = Field(default_factory=list)


class InspirationAttachment(BaseModel):
    id: str
    asset_id: str | None = None
    type: Literal["image", "video", "text", "transcript"]
    name: str | None = None
    preview_url: str | None = None
    status: Literal["ready", "processing", "failed"]
    usage_type: Literal["style_reference", "content_asset"] | None = None


class InspirationMessage(BaseModel):
    id: str
    role: Literal["assistant", "user", "system"]
    content: str
    options: StyleOptionBlock | None = None
    fallback_used: bool | None = None
    attachments: list[InspirationAttachment] = Field(default_factory=list)
    asset_candidates: dict[str, Any] | None = None
    style_context: dict[str, Any] | None = None
    created_at: str


class InspirationAssetCandidates(BaseModel):
    foods: list[str] = Field(default_factory=list)
    scenes: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    source_asset_ids: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)


class InspirationStyleContext(BaseModel):
    style_profile_id: str | None = None
    style_name: str | None = None
    sample_image_asset_id: str | None = None
    sample_image_preview_url: str | None = None
    style_payload: StylePayload | None = None


class InspirationAllocationPlanItem(BaseModel):
    slot_index: int = Field(ge=1, le=10)
    focus_title: str
    focus_description: str
    locations: list[str] = Field(default_factory=list)
    scenes: list[str] = Field(default_factory=list)
    foods: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    source_asset_ids: list[str] = Field(default_factory=list)
    confirmed: bool = False


class InspirationDraft(BaseModel):
    stage: Literal["style_collecting", "prompt_revision", "asset_confirming", "locked"]
    style_payload: StylePayload | None = None
    image_count: int | None = Field(default=None, ge=1, le=10)
    draft_style_id: str | None = None
    allocation_plan: list[InspirationAllocationPlanItem] = Field(default_factory=list)
    locked: bool


class InspirationConversationResponse(BaseModel):
    session_id: str
    messages: list[InspirationMessage]
    draft: InspirationDraft


class StyleProfile(BaseModel):
    id: str
    session_id: str | None = None
    name: str
    style_payload: StylePayload
    sample_image_preview_url: str | None = None
    is_builtin: bool
    created_at: str
    updated_at: str


class StyleProfileListResponse(BaseModel):
    items: list[StyleProfile]


class GenerationJob(BaseModel):
    id: str
    session_id: str
    style_profile_id: str | None = None
    image_count: int
    status: Literal["queued", "running", "partial_success", "success", "failed", "canceled"]
    progress_percent: int
    current_stage: Literal[
        "asset_extract",
        "asset_allocate",
        "prompt_generate",
        "image_generate",
        "copy_generate",
        "finalize",
    ]
    stage_message: str
    error_code: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str


class GenerationStageItem(BaseModel):
    stage: str
    status: str
    stage_message: str
    created_at: str


class GenerationStageListResponse(BaseModel):
    job_id: str
    items: list[GenerationStageItem]


class AssetBreakdownSourceAsset(BaseModel):
    asset_id: str
    asset_type: str
    content: str | None = None


class AssetBreakdownExtracted(BaseModel):
    foods: list[str] = Field(default_factory=list)
    scenes: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class JobAssetBreakdownResponse(BaseModel):
    job_id: str
    session_id: str
    content_mode: Literal["food", "scenic", "food_scenic"]
    source_assets: list[AssetBreakdownSourceAsset] = Field(default_factory=list)
    extracted: AssetBreakdownExtracted
    created_at: str


class ImageResult(BaseModel):
    image_index: int
    asset_refs: list[str] = Field(default_factory=list)
    prompt_text: str
    image_url: str


class CopySection(BaseModel):
    heading: str
    content: str


class CopyResult(BaseModel):
    title: str
    intro: str
    guide_sections: list[CopySection] = Field(default_factory=list)
    ending: str
    full_text: str


class GenerationResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    job_id: str
    status: str
    images: list[ImageResult]
    copy_result: CopyResult = Field(alias="copy")


class ExportTask(BaseModel):
    id: str
    session_id: str
    job_id: str
    export_format: str
    status: Literal["queued", "running", "success", "failed"]
    file_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: str


class Provider(BaseModel):
    id: str
    name: str
    base_url: str
    api_key_masked: str
    api_protocol: str
    enabled: bool
    created_at: str
    updated_at: str


class ProviderListResponse(BaseModel):
    items: list[Provider]


class ModelInfo(BaseModel):
    id: str
    name: str
    capabilities: list[Literal["image_generation", "text_generation", "vision"]]


class ModelListResponse(BaseModel):
    provider_id: str
    items: list[ModelInfo]


class ModelReference(BaseModel):
    provider_id: str
    model_name: str


class ModelRoutingConfig(BaseModel):
    image_model: ModelReference
    text_model: ModelReference
    updated_at: str


class DeleteResponse(BaseModel):
    deleted: bool


class SessionDetailResponse(BaseModel):
    session: Session
    assets: list[Asset]
    jobs: list[GenerationJob]
    exports: list[ExportTask]
