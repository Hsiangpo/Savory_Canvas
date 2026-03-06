from __future__ import annotations


def test_load_prompt_reads_template_text():
    from backend.app.core.prompt_loader import load_prompt

    text = load_prompt("inspiration/style_prompt_system_prompt.txt")
    assert "资深视觉创意总监" in text


def test_load_prompt_uses_cache():
    from backend.app.core.prompt_loader import load_prompt

    first = load_prompt("inspiration/style_prompt_system_prompt.txt")
    second = load_prompt("inspiration/style_prompt_system_prompt.txt")
    assert first is second
