
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _FakeChatResponse:
    content: str
    reasoning_summaries: list[str] = field(default_factory=list)


class _FakeChatModel:
    def __init__(self, decisions: list[dict[str, Any]]):
        self._decisions = list(decisions)
        self.calls: list[list[Any]] = []

    def invoke(self, messages: list[Any]) -> _FakeChatResponse:
        self.calls.append(messages)
        index = len(self.calls) - 1
        payload = dict(self._decisions[index])
        reasoning_summaries = payload.pop("__reasoning_summaries", [])
        import json
        return _FakeChatResponse(
            content=json.dumps(payload, ensure_ascii=False),
            reasoning_summaries=list(reasoning_summaries) if isinstance(reasoning_summaries, list) else [],
        )


class _FakeLLMProvider:
    def __init__(self, decisions: list[dict[str, Any]]):
        self.model = _FakeChatModel(decisions)

    def build_chat_model(self) -> _FakeChatModel:
        return self.model


class _ToolStub:
    def __init__(self, fn):
        self._fn = fn

    def invoke(self, args: dict[str, Any]) -> Any:
        return self._fn(args)

def test_creative_agent_redirects_redundant_extract_assets_without_new_content_input(tmp_path):
    from backend.app.agent.creative_agent import CreativeAgent

    decisions = [
        {
            "decision": "use_tool",
            "reason": "继续提取素材。",
            "tool_name": "extract_assets",
            "tool_args": {"session_id": "session-1"},
        },
        {
            "decision": "respond_directly",
            "reason": "提示词已整理。",
            "result": {
                "reply": "我已经把提示词整理好了。",
                "stage": "prompt_ready",
                "locked": False,
            },
        },
    ]
    observed = {"extract": 0, "prompt": 0}
    agent = CreativeAgent(
        llm_provider=_FakeLLMProvider(decisions),
        tools={
            "extract_assets": _ToolStub(lambda _: observed.__setitem__("extract", observed["extract"] + 1) or {}),
            "generate_style_prompt": _ToolStub(
                lambda _: observed.__setitem__("prompt", observed["prompt"] + 1)
                or {"style_prompt": "生成一张西安城市漫游图。", "image_count": None}
            ),
        },
        db_path=tmp_path / "agent-redundant-extract.db",
    )

    events = list(
        agent.respond_stream(
            {
                "session_id": "session-1",
                "text": "",
                "selected_items": ["沿用西安这组内容继续"],
                "action": "continue_existing_content",
                "attachments": [],
                "state": {
                    "stage": "content_confirmation",
                    "locked": False,
                    "asset_candidates": {
                        "locations": ["西安"],
                        "scenes": ["老菜场", "中流巷", "书院门"],
                        "foods": [],
                        "keywords": ["西安街区漫游"],
                        "source_asset_ids": ["video-1", "transcript-1"],
                    },
                    "style_prompt": "",
                    "allocation_plan": [],
                },
            }
        )
    )

    assert observed["extract"] == 0
    assert observed["prompt"] == 1
    assert any(event["event"] == "tool_start" and event["data"]["tool_name"] == "generate_style_prompt" for event in events)

