import json
from pathlib import Path

import pytest

from app.autonomy.planner import (
    DecomposedTask,
    GoalDecompositionService,
    GoalRequest,
    PlannerOutput,
)
from app.autonomy.service import AutonomousContinuationService
from app.navigation.service import WorkspaceNavigator
from app.plans.execution import PlanExecutionCoordinator
from app.plans.service import PlanService
from app.policy.workspaces import WorkspaceAccessError, WorkspaceRegistry
from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse
from app.providers.registry import ProviderRegistry
from app.tools.registry import ToolRegistry


class PlannerProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(self, content: str) -> None:
        self.content = content

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(provider=self.name, model=request.model, content=self.content)

    async def health_check(self) -> bool:
        return True


def service(tmp_path: Path, content: str) -> GoalDecompositionService:
    root = tmp_path / "repo"; root.mkdir(); (root / "README.md").write_text("repo", encoding="utf-8")
    workspaces = WorkspaceRegistry({"repo": root})
    plans = PlanService(tmp_path / "state.sqlite3")
    tools = ToolRegistry()
    autonomy = AutonomousContinuationService(plans, PlanExecutionCoordinator(plans, tools))
    providers = ProviderRegistry(); providers.register(PlannerProvider(content))
    return GoalDecompositionService(providers, WorkspaceNavigator(workspaces), autonomy)


def task(key: str, deps: list[str] | None = None, worker: str = "direct") -> dict:
    return {"key": key, "title": key, "objective": key, "dependencies": deps or [], "expected_evidence": "result", "worker": worker, "tool_category": "read", "verification": "check result", "mutation_required": False}


@pytest.mark.asyncio
async def test_goal_decomposition_maps_and_persists_graph(tmp_path: Path) -> None:
    planner = service(tmp_path, json.dumps({"tasks": [task("map"), task("tests", ["map"], "code_analyst")] }))
    output, plan = await planner.decompose(GoalRequest(goal="inspect tests", scope="s", workspace="repo"), provider_name="openai_compatible", model="test")
    assert [item.key for item in output.tasks] == ["map", "tests"]
    assert plan.metadata["original_goal"] == "inspect tests"
    assert plan.steps[1].metadata["depends_on_positions"] == [0]


def test_planner_rejects_cycles_and_unknown_workers(tmp_path: Path) -> None:
    planner = service(tmp_path, "{}")
    with pytest.raises(ValueError, match="cycle"):
        planner._validate(PlannerOutput(tasks=[DecomposedTask(**task("a", ["b"])), DecomposedTask(**task("b", ["a"]))]))
    with pytest.raises(ValueError, match="worker"):
        planner._validate(PlannerOutput(tasks=[DecomposedTask(**task("a", worker="unknown"))]))


@pytest.mark.asyncio
async def test_planner_rejects_missing_workspace_before_provider(tmp_path: Path) -> None:
    planner = service(tmp_path, "{}")
    with pytest.raises(WorkspaceAccessError):
        await planner.decompose(GoalRequest(goal="inspect", scope="s", workspace="missing"), provider_name="openai_compatible", model="test")
