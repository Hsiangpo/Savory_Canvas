from __future__ import annotations

GENERATION_STAGES = [
    "asset_extract",
    "asset_allocate",
    "prompt_generate",
    "image_generate",
    "copy_generate",
    "finalize",
]

FINAL_JOB_STATUSES = {"success", "partial_success", "failed", "canceled"}
FINAL_EXPORT_STATUSES = {"success", "failed"}

STYLE_STAGE_ORDER = [
    "painting_style",
    "background_decor",
    "color_mood",
    "image_count",
]
