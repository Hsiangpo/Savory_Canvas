from __future__ import annotations

from backend.app.core.prompt_loader import load_prompt

WELCOME_MESSAGE = "欢迎来到 Savory Canvas！把你的灵感发给我吧，文字、图片和视频都可以，我会帮你整理成可生成的创作方案。"
ASSET_CONFIRM_HINT = "已确认风格提示词。下面是每张图重点内容建议，请按你的想法调整后再确认锁定。"
STYLE_PROMPT_SYSTEM_PROMPT = load_prompt("inspiration/style_prompt_system_prompt.txt")
STYLE_PROMPT_RETRY_SYSTEM_PROMPT = load_prompt("inspiration/style_prompt_retry_system_prompt.txt")
VISION_ERROR_MESSAGE = "当前模型不支持图片解析，请切换为视觉模型后重试。"
ASSET_EXTRACT_SYSTEM_PROMPT = load_prompt("inspiration/asset_extract_system_prompt.txt")
IMAGE_ASSET_EXTRACT_SYSTEM_PROMPT = load_prompt("inspiration/image_asset_extract_system_prompt.txt")
ALLOCATION_PLAN_SYSTEM_PROMPT = load_prompt("inspiration/allocation_plan_system_prompt.txt")
STYLE_SAVE_SUMMARY_SYSTEM_PROMPT = load_prompt("inspiration/style_save_summary_system_prompt.txt")
