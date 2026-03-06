from __future__ import annotations

from backend.app.core.prompt_loader import load_prompt

WELCOME_MESSAGE = "欢迎来到 Savory Canvas！把你的灵感发给我吧，文字、图片和视频都可以，我会帮你整理成可生成的创作方案。"
LOCKED_HINT = "已确定当前风格与资产，可开始生成。是否保存风格参数和提示词？"
ASSET_CONFIRM_HINT = "已确认风格提示词。下面是每张图重点内容建议，请按你的想法调整后再确认锁定。"
STYLE_REQUIREMENT_HINT = (
    "已应用该风格。为了生成更贴合的提示词，请先补充你的创作需求："
    "例如城市/地区、核心景点或美食、想突出哪些内容，以及计划生成几张图。"
    "你也可以继续上传图片或视频作为参考。"
)
STYLE_REQUIREMENT_SYSTEM_PROMPT = load_prompt("inspiration/style_requirement_system_prompt.txt")
STYLE_PROMPT_SYSTEM_PROMPT = load_prompt("inspiration/style_prompt_system_prompt.txt")
STYLE_PROMPT_RETRY_SYSTEM_PROMPT = load_prompt("inspiration/style_prompt_retry_system_prompt.txt")
PROMPT_READINESS_SYSTEM_PROMPT = load_prompt("inspiration/prompt_readiness_system_prompt.txt")
IMAGE_COUNT_EXTRACT_SYSTEM_PROMPT = load_prompt("inspiration/image_count_extract_system_prompt.txt")
VISION_ERROR_MESSAGE = "当前模型不支持图片解析，请切换为视觉模型后重试。"
ASSET_EXTRACT_SYSTEM_PROMPT = load_prompt("inspiration/asset_extract_system_prompt.txt")
IMAGE_ASSET_EXTRACT_SYSTEM_PROMPT = load_prompt("inspiration/image_asset_extract_system_prompt.txt")
ALLOCATION_PLAN_SYSTEM_PROMPT = load_prompt("inspiration/allocation_plan_system_prompt.txt")
PROMPT_ACTION_OPTIONS = {"title": "请选择下一步", "items": ["确认提示词"], "max": 1}
STYLE_SAVE_SUMMARY_SYSTEM_PROMPT = load_prompt("inspiration/style_save_summary_system_prompt.txt")
