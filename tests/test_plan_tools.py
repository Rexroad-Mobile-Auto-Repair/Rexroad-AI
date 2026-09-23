from pathlib import Path

import pytest

from app.plans.models import PlanCreate, PlanStepCreate
from app.plans.service import PlanService
from app.policy.workspaces import WorkspaceRegistry
from app.tools.factory import build_tool_registry
from app.tools.filesystem import ReadOnlyFilesystem
from app.tools.git import ReadOnlyGit
from app.tools.plans import PlanTools


def make_plan(tmp_path: Path):
    service = PlanService(tmp_path / "plans.sqlite3")
    plan = service.create(PlanCreate(scope="a", goal="ship", steps=[PlanStepCreate(title="one"), PlanStepCreate(title="two")]))
    return service, plan


def test_plan_tools_read_and_update_with_scope_and_references(tmp_path: Path) -> None:
    service, plan = make_plan(tmp_path)
    tools = PlanTools(service)
    assert tools.list("a")[0]["id"] == plan.id
    assert tools.get("a", plan.id)["steps"][0]["position"] == 0
    assert tools.next_step("a", plan.id)["id"] == plan.steps[0].id
    updated = tools.update_step("a", plan.id, plan.steps[0].id, "in_progress", "session-1")
    assert updated["steps"][0]["reference"] == "session-1"
    assert tools.next_step("a", plan.id)["status"] == "in_progress"


def test_plan_tools_scope_and_transition_errors(tmp_path: Path) -> None:
    service, plan = make_plan(tmp_path)
    tools = PlanTools(service)
    assert tools.list("b") == []
    with pytest.raises(ValueError):
        tools.get("b", plan.id)
    with pytest.raises(ValueError):
        tools.update_step("a", plan.id, plan.steps[0].id, "completed")


def test_registry_contains_only_controlled_plan_surface(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    service, _ = make_plan(tmp_path)
    registry = build_tool_registry(ReadOnlyFilesystem(WorkspaceRegistry({"repo": root})), ReadOnlyGit(WorkspaceRegistry({"repo": root})), plans=service)
    assert {name for name in registry.names() if name.startswith("plan.")} == {"plan.list", "plan.get", "plan.next_step", "plan.update_step"}
    assert registry.get("plan.list").permission == "read"
    assert registry.get("plan.update_step").permission == "plan_write"
    assert "plan.execute" not in registry.names()
