from pathlib import Path

import pytest

from app.agents.models import AgentQueryRequest
from app.agents.service import AgentLoopLimitError, AgentService
from app.config import Settings
from app.journal.store import ActionJournal
from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse
from app.providers.registry import ProviderRegistry
from app.tools.models import ToolCall
from app.tools.registry import ToolDefinition, ToolRegistry


class PolicyProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(self, calls: list[ToolCall] | None = None) -> None:
        self.calls = calls or []
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.calls:
            return ModelResponse(provider=self.name, model=request.model, tool_calls=[self.calls.pop(0)])
        return ModelResponse(provider=self.name, model=request.model, content="direct answer")

    async def health_check(self) -> bool:
        return True


def build(tmp_path: Path, provider: PolicyProvider, tools: ToolRegistry) -> AgentService:
    registry = ProviderRegistry()
    registry.register(provider)
    return AgentService(Settings(_env_file=None), registry, tools=tools, journal=ActionJournal(tmp_path / "journal.sqlite3"))


@pytest.mark.asyncio
async def test_read_only_intent_excludes_checks_and_mutations(tmp_path: Path) -> None:
    provider = PolicyProvider()
    tools = ToolRegistry()
    for name, permission, high in (("filesystem.read", "read", False), ("workspace.run_check", "workspace_check", False), ("filesystem.apply_patch", "filesystem_write", True)):
        tools.register(ToolDefinition(name=name, description=name, permission=permission, high_impact=high, handler=lambda: "ok"))
    service = build(tmp_path, provider, tools)
    await service.query(AgentQueryRequest(message="Inspect this project. Do not modify anything.", workspace="workspace"))
    assert [spec.name for spec in provider.requests[0].tools] == ["filesystem.read"]


@pytest.mark.asyncio
async def test_explicit_test_request_exposes_check_only(tmp_path: Path) -> None:
    provider = PolicyProvider()
    tools = ToolRegistry()
    tools.register(ToolDefinition(name="workspace.run_check", description="check", permission="workspace_check", handler=lambda **_: "ok"))
    tools.register(ToolDefinition(name="filesystem.apply_patch", description="write", permission="filesystem_write", high_impact=True, handler=lambda **_: "ok"))
    service = build(tmp_path, provider, tools)
    await service.query(AgentQueryRequest(message="Run the relevant tests and tell me whether they pass.", workspace="workspace"))
    assert [spec.name for spec in provider.requests[0].tools] == ["workspace.run_check"]


@pytest.mark.asyncio
async def test_repeated_same_tool_result_stops_without_unbounded_churn(tmp_path: Path) -> None:
    call = ToolCall(id="same", name="filesystem.read", arguments={"workspace": "w", "relative_path": "x"})
    provider = PolicyProvider([call, call, call, call])
    tools = ToolRegistry()
    tools.register(ToolDefinition(name="filesystem.read", description="read", permission="read", handler=lambda **_: "same"))
    service = build(tmp_path, provider, tools)
    with pytest.raises(AgentLoopLimitError, match="no progress"):
        await service.query(AgentQueryRequest(message="inspect", workspace="w"))
