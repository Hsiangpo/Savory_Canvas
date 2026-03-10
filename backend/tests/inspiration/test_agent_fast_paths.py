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


class _ExplodingLLMProvider:
    def build_chat_model(self):
        raise AssertionError("should not call llm")


class _ToolStub:
    def __init__(self, fn):
        self._fn = fn

    def invoke(self, args: dict[str, Any]) -> Any:
        return self._fn(args)


def test_creative_agent_preserves_allocation_plan_from_previous_tool(tmp_path):
    from backend.app.agent.creative_agent import CreativeAgent

    allocation_plan = [
        {"slot_index": 1, "focus_title": "老菜场 + 中流巷"},
        {"slot_index": 2, "focus_title": "书院门 + 碑林博物馆"},
    ]
    decisions = [
        {
            "decision": "use_tool",
            "reason": "先完成分图规划。",
            "tool_name": "allocate_assets_to_images",
            "tool_args": {"session_id": "session-1", "user_hint": "继续"},
        },
        {
            "decision": "respond_directly",
            "reason": "分图方案已经准备好，给用户确认。",
            "result": {
                "reply": "分图方案已经整理好了。",
                "stage": "allocation_confirmation",
                "locked": False,
                "progress": 75,
                "progress_label": "分图规划完成",
            },
        },
    ]
    agent = CreativeAgent(
        llm_provider=_FakeLLMProvider(decisions),
        tools={
            "allocate_assets_to_images": _ToolStub(lambda _: allocation_plan),
        },
        db_path=tmp_path / "agent-fast-paths.db",
    )

    result = agent.respond(
        {
            "session_id": "session-1",
            "text": "继续",
            "selected_items": [],
            "action": "confirm_image_count_2",
            "attachments": [],
            "state": {
                "stage": "count_confirmation_required",
                "locked": False,
                "image_count": 2,
                "style_payload": {},
                "style_prompt": "生成一张：西安街区漫游图。",
                "asset_candidates": {"scenes": ["老菜场", "书院门"]},
                "allocation_plan": [],
            },
        }
    )

    assert result["stage"] == "allocation_confirmation"
    assert result["allocation_plan"] == allocation_plan


def test_creative_agent_confirm_and_generate_bypasses_llm_when_allocation_ready(tmp_path):
    from backend.app.agent.creative_agent import CreativeAgent

    observed = {"generate_images": 0}
    agent = CreativeAgent(
        llm_provider=_ExplodingLLMProvider(),
        tools={
            "generate_images": _ToolStub(
                lambda _: observed.__setitem__("generate_images", observed["generate_images"] + 1)
                or {"job_id": "job-1", "status": "queued"}
            ),
        },
        db_path=tmp_path / "agent-fast-paths.db",
    )

    events = list(
        agent.respond_stream(
            {
                "session_id": "session-1",
                "text": "",
                "selected_items": ["方案很棒，开始出图"],
                "action": "confirm_and_generate",
                "attachments": [],
                "state": {
                    "stage": "allocation_confirmation",
                    "locked": False,
                    "image_count": 3,
                    "style_payload": {},
                    "style_prompt": "生成一张：西安街区漫游图。",
                    "asset_candidates": {"scenes": ["老菜场", "书院门", "德福巷"]},
                    "allocation_plan": [
                        {"slot_index": 1, "focus_title": "老菜场 + 中流巷"},
                        {"slot_index": 2, "focus_title": "书院门 + 碑林博物馆"},
                        {"slot_index": 3, "focus_title": "德福巷 + 太阳庙门"},
                    ],
                    "active_job_id": None,
                },
            }
        )
    )

    assert observed["generate_images"] == 1
    assert any(event["event"] == "tool_start" and event["data"]["tool_name"] == "generate_images" for event in events)
    final_event = events[-1]
    assert final_event["event"] == "result"
    assert final_event["data"]["active_job_id"] == "job-1"


def test_creative_agent_confirm_and_generate_rebuilds_allocation_when_missing(tmp_path):
    from backend.app.agent.creative_agent import CreativeAgent

    decisions = [
        {
            "decision": "respond_directly",
            "reason": "这里不该再走模型。",
            "result": {"reply": "不应触发", "stage": "error", "locked": False},
        },
    ]
    observed = {"allocate": 0, "generate": 0}
    agent = CreativeAgent(
        llm_provider=_FakeLLMProvider(decisions),
        tools={
            "allocate_assets_to_images": _ToolStub(
                lambda _: observed.__setitem__("allocate", observed["allocate"] + 1)
                or [{"slot_index": 1, "focus_title": "老菜场 + 中流巷"}]
            ),
            "generate_images": _ToolStub(
                lambda _: observed.__setitem__("generate", observed["generate"] + 1)
                or {"job_id": "job-2", "status": "queued"}
            ),
        },
        db_path=tmp_path / "agent-fast-paths.db",
    )

    events = list(
        agent.respond_stream(
            {
                "session_id": "session-1",
                "text": "",
                "selected_items": ["方案很棒，开始出图"],
                "action": "confirm_and_generate",
                "attachments": [],
                "state": {
                    "stage": "allocation_confirmation",
                    "locked": False,
                    "image_count": 3,
                    "style_payload": {},
                    "style_prompt": "生成一张：西安街区漫游图。",
                    "asset_candidates": {"scenes": ["老菜场", "中流巷", "书院门"]},
                    "allocation_plan": [],
                    "active_job_id": None,
                },
            }
        )
    )

    assert observed["allocate"] == 1
    assert observed["generate"] == 1
    assert [event["data"]["tool_name"] for event in events if event["event"] == "tool_start"] == [
        "allocate_assets_to_images",
        "generate_images",
    ]
    assert events[-1]["event"] == "result"
    assert events[-1]["data"]["active_job_id"] == "job-2"


def test_creative_agent_image_count_action_bypasses_llm_and_allocates(tmp_path):
    from backend.app.agent.creative_agent import CreativeAgent

    observed = {"allocate": 0}
    agent = CreativeAgent(
        llm_provider=_ExplodingLLMProvider(),
        tools={
            "allocate_assets_to_images": _ToolStub(
                lambda _: observed.__setitem__("allocate", observed["allocate"] + 1)
                or [{"slot_index": 1, "focus_title": "老菜场文创园"}]
            ),
        },
        db_path=tmp_path / "agent-fast-paths.db",
    )

    events = list(
        agent.respond_stream(
            {
                "session_id": "session-1",
                "text": "",
                "selected_items": ["做4张图，精选几个景点"],
                "action": "set_image_count_4",
                "attachments": [],
                "state": {
                    "stage": "confirming_image_count",
                    "locked": False,
                    "image_count": 4,
                    "style_payload": {},
                    "style_prompt": "请保持统一风格与清晰图文布局。",
                    "asset_candidates": {"scenes": ["老菜场", "中流巷", "书院门", "德福巷"]},
                    "allocation_plan": [],
                    "active_job_id": None,
                },
            }
        )
    )

    assert observed["allocate"] == 1
    assert any(event["event"] == "tool_start" and event["data"]["tool_name"] == "allocate_assets_to_images" for event in events)
