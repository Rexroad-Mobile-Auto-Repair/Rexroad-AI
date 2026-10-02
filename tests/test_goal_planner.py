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
        self.request = request
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


def service_with_provider(tmp_path: Path, content: str) -> tuple[GoalDecompositionService, PlannerProvider]:
    root = tmp_path / "repo"; root.mkdir(); (root / "example.py").write_text("def greeting(name):\n    return f'Hello, {name}'\n", encoding="utf-8")
    (root / "tests").mkdir(); (root / "tests" / "test_example.py").write_text("def test_greeting():\n    assert greeting('Aaron') == 'Hello, Aaron'\n", encoding="utf-8")
    workspaces = WorkspaceRegistry({"repo": root})
    plans = PlanService(tmp_path / "state.sqlite3")
    provider = PlannerProvider(content)
    providers = ProviderRegistry(); providers.register(provider)
    autonomy = AutonomousContinuationService(plans, PlanExecutionCoordinator(plans, ToolRegistry()))
    return GoalDecompositionService(providers, WorkspaceNavigator(workspaces), autonomy), provider


def task(key: str, deps: list[str] | None = None, worker: str = "direct") -> dict:
    return {"key": key, "title": key, "objective": key, "dependencies": deps or [], "expected_evidence": "result", "worker": worker, "tool_category": "read", "verification": "check result", "mutation_required": False}


@pytest.mark.asyncio
async def test_read_only_chat_goal_rejects_mutation_before_persistence(tmp_path):
    planner = service(tmp_path, json.dumps({"tasks": [task("change", worker="supervised_coding")]}))
    with pytest.raises(ValueError, match="read-only planning"):
        await planner.decompose(GoalRequest(goal="Read-only inspection", scope="chat:s", workspace="repo", read_only=True), provider_name="openai_compatible", model="test")
    assert planner._autonomy._plans.list("chat:s") == []


@pytest.mark.asyncio
async def test_read_only_direct_inspection_cannot_be_reclassified_as_research(tmp_path):
    planner = service(tmp_path, json.dumps({"tasks": [{**task("inspect"), "objective": "Investigate local source"}]}))
    _, plan = await planner.decompose(GoalRequest(goal="Read-only inspection", scope="chat:s", workspace="repo", read_only=True), provider_name="openai_compatible", model="test")
    assert plan.steps[0].metadata["worker"] == "direct"


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


@pytest.mark.asyncio
async def test_planner_prompt_contains_live_files_and_persists_evidence(tmp_path: Path) -> None:
    content = json.dumps({"tasks": [task("inspect"), task("code", ["inspect"], "supervised_coding")]})
    planner, provider = service_with_provider(tmp_path, content)
    _, plan = await planner.decompose(GoalRequest(goal="add a test", scope="s", workspace="repo"), provider_name="openai_compatible", model="test")
    prompt = provider.request.messages[0].content
    assert "example.py" in prompt and "tests/test_example.py" in prompt
    assert plan.metadata["preplan_evidence"]["operation"] == "map"
    assert "example.py" in plan.metadata["preplan_evidence"]["files"]


def test_normalize_coalesces_duplicate_coding_and_internal_verifier_tasks(tmp_path: Path) -> None:
    planner = service(tmp_path, "{}")
    output = PlannerOutput(tasks=[
        DecomposedTask(**task("inspect")),
        DecomposedTask(**{**task("code", ["inspect"], "supervised_coding"), "objective": "Add the coding change"}),
        DecomposedTask(**{**task("code_again", ["code"], "supervised_coding"), "title": "code", "objective": "Add the coding change through supervised coding workflow"}),
        DecomposedTask(**{**task("verify", ["code"], "verifier"), "title": "Verify coding result", "objective": "Verify the coding change result and report"}),
    ])
    normalized = planner._normalize(output)
    assert [item.key for item in normalized.tasks] == ["inspect", "code"]


def test_normalize_rejects_or_maps_provider_tool_categories(tmp_path: Path) -> None:
    planner = service(tmp_path, "{}")
    mapped = planner._normalize(PlannerOutput(tasks=[DecomposedTask(**{**task("inspect", worker="code_analyst"), "tool_category": "analysis"})]))
    assert mapped.tasks[0].tool_category == "read"
    with pytest.raises(ValueError, match="tool category"):
        planner._normalize(PlannerOutput(tasks=[DecomposedTask(**{**task("inspect", worker="supervised_coding"), "tool_category": "invented", "mutation_required": True})]))
