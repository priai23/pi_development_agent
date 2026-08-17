from types import SimpleNamespace

import pytest

from agent import ERPImplementationAgent, tool_outcome


def test_structured_tool_outcomes_are_truthful():
    assert tool_outcome("verify_module_installation", '{"passed": true}') == "succeeded"
    assert tool_outcome("verify_module_installation", '{"passed": false}') == "failed"
    assert tool_outcome("run_project_check", '{"exit_code": 1}') == "failed"
    assert tool_outcome("deploy", '{"status": "failed"}') == "failed"
    assert tool_outcome("read_file", "plain output") == "succeeded"


class _Executor:
    def __init__(self, tool_name: str):
        self.state = SimpleNamespace(
            next=("tools",),
            values={"messages": [SimpleNamespace(tool_calls=[{"name": tool_name, "args": {}, "id": "call-1"}])]},
        )

    async def aget_state(self, _config):
        return self.state


@pytest.mark.asyncio
async def test_unknown_tool_is_not_misclassified_as_an_approval():
    agent = object.__new__(ERPImplementationAgent)
    agent.executor = _Executor("emit thinking")

    assert await agent.pending_call("thread") is None


@pytest.mark.asyncio
async def test_registered_risky_tool_still_requires_approval():
    agent = object.__new__(ERPImplementationAgent)
    agent.executor = _Executor("write_file")

    assert (await agent.pending_call("thread"))["name"] == "write_file"
