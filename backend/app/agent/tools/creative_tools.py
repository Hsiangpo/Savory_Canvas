from __future__ import annotations

from typing import Any

from langchain.tools import tool


def build_creative_tools(runtime: Any) -> dict[str, Any]:
    @tool
    def suggest_painting_style(stage: str, user_reply: str, selected_items: list[str]) -> dict[str, Any]:
        """根据当前风格收集阶段，生成下一轮风格对话建议。"""
        return runtime.suggest_painting_style(stage=stage, user_reply=user_reply, selected_items=selected_items)

    @tool
    def extract_assets(session_id: str, user_hint: str, style_prompt: str) -> dict[str, Any]:
        """从文本、图片与视频转写中提取本次创作资产候选。"""
        return runtime.extract_assets(session_id=session_id, user_hint=user_hint, style_prompt=style_prompt)

    @tool
    def generate_style_prompt(session_id: str, feedback: str) -> dict[str, Any]:
        """根据当前草稿风格和反馈生成母提示词。"""
        return runtime.generate_style_prompt(session_id=session_id, feedback=feedback)

    @tool
    def allocate_assets_to_images(session_id: str, user_hint: str) -> list[dict[str, Any]]:
        """将提取到的资产分配到每一张目标图片。"""
        return runtime.allocate_assets_to_images(session_id=session_id, user_hint=user_hint)

    @tool
    def save_style(session_id: str) -> dict[str, Any]:
        """将当前锁定草案保存为可复用的风格模板。"""
        return runtime.save_style_from_agent(session_id=session_id)

    @tool
    def generate_images(session_id: str) -> dict[str, Any]:
        """按当前会话锁定草案创建图片生成任务。"""
        return runtime.generate_images(session_id=session_id)

    @tool
    def generate_copy(job_id: str) -> dict[str, Any]:
        """按当前任务配置执行文案生成。"""
        return runtime.generate_copy(job_id=job_id)

    return {
        "suggest_painting_style": suggest_painting_style,
        "extract_assets": extract_assets,
        "generate_style_prompt": generate_style_prompt,
        "allocate_assets_to_images": allocate_assets_to_images,
        "save_style": save_style,
        "generate_images": generate_images,
        "generate_copy": generate_copy,
    }
