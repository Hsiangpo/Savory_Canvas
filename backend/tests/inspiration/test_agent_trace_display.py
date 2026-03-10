from __future__ import annotations

import threading
from typing import Any


class _ToolStub:
    def __init__(self, fn):
        self._fn = fn

    def invoke(self, args: dict[str, Any]) -> Any:
        return self._fn(args)


def test_tool_trace_summary_uses_localized_message_for_extract_assets():
    from backend.app.agent.creative_agent import CreativeAgent

    agent = CreativeAgent.__new__(CreativeAgent)
    agent._stream_local = threading.local()
    agent.tools = {
        "extract_assets": _ToolStub(lambda _: {"locations": ["成都"], "foods": ["回锅肉"], "scenes": ["巷子生活"]}),
    }

    result = agent._tool_node(
        {
            "decision": {
                "decision": "use_tool",
                "tool_name": "extract_assets",
                "tool_args": {},
            },
            "request": {
                "session_id": "session-1",
                "text": "帮我提取素材",
                "state": {"stage": "asset_ready", "style_prompt": "复古旅行手账", "locked": False},
            },
            "trace": [{"id": "trace-1", "node": "agent_node", "status": "planned"}],
        }
    )

    tool_trace = result["trace"][-1]
    assert tool_trace["tool_name"] == "extract_assets"
    assert tool_trace["summary"].startswith("已提取素材")
    assert "extract_assets" not in tool_trace["summary"]
