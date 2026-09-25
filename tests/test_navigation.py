from pathlib import Path

import pytest

from app.agents.models import AgentQueryRequest
from app.agents.service import AgentService
from app.config import Settings
from app.navigation.service import WorkspaceNavigator
from app.policy.workspaces import WorkspaceAccessError, WorkspaceRegistry
from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse
from app.providers.registry import ProviderRegistry
from app.tools.models import ToolCall
from app.tools.registry import ToolDefinition, ToolRegistry


def make_workspace(tmp_path: Path) -> tuple[WorkspaceRegistry, Path]:
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "app" / "entry.py").write_text("class Navigator:\n    def locate(self):\n        return True\n", encoding="utf-8")
    (root / "tests" / "test_entry.py").write_text("from app.entry import Navigator\n", encoding="utf-8")
    return WorkspaceRegistry({"repo": root}), root


def test_navigator_map_symbols_related_tests_and_git(tmp_path: Path) -> None:
    workspaces, root = make_workspace(tmp_path)
    navigator = WorkspaceNavigator(workspaces)
    assert "app" in navigator.navigate("repo")["top_level"]
    symbol = navigator.navigate("repo", "find_symbol", "locate")["matches"][0]
    assert symbol["file"] == "app/entry.py"
    assert navigator.navigate("repo", "find_symbol", "Navigator symbol")["matches"]
    assert navigator.navigate("repo", "related", "entry")["files"]
    assert navigator.navigate("repo", "tests", "Navigator")["tests"]
    assert navigator.navigate("repo", "git")["git"]["head"] == ""
    assert root.exists()


def test_navigator_is_bounded_and_missing_queries_are_safe(tmp_path: Path) -> None:
    workspaces, _ = make_workspace(tmp_path)
    navigator = WorkspaceNavigator(workspaces)
    result = navigator.navigate("repo", "find_symbol", "missing")
    assert result["matches"] == []
    assert len(str(navigator.navigate("repo"))) < 12000
    with pytest.raises(WorkspaceAccessError):
        navigator.navigate("other")


class NavigatorProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelResponse(provider=self.name, model=request.model, tool_calls=[ToolCall(id="map-1", name="workspace.repo_map", arguments={"workspace": "repo", "operation": "find_symbol", "query": "Navigator"})])
        return ModelResponse(provider=self.name, model=request.model, content="Navigator is in app/entry.py.")

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_agent_uses_navigator_through_normal_read_tool_boundary(tmp_path: Path) -> None:
    workspaces, _ = make_workspace(tmp_path)
    navigator = WorkspaceNavigator(workspaces)
    tools = ToolRegistry()
    tools.register(ToolDefinition(name="workspace.repo_map", description="map", permission="read", handler=navigator.navigate, parameters={"type": "object"}))
    provider = NavigatorProvider()
    providers = ProviderRegistry(); providers.register(provider)
    service = AgentService(Settings(_env_file=None), providers, tools=tools, allow_tools_without_workspace=False)
    result = await service.query(AgentQueryRequest(message="Where is the Navigator symbol?", workspace="repo"))
    assert result.content == "Navigator is in app/entry.py."
    assert provider.requests[0].tools[0].name == "workspace.repo_map"
