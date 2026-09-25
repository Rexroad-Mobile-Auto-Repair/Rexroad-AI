from pathlib import Path

import pytest

from app.autonomy.service import AutonomousContinuationService, ContinuationBounds
from app.plans.execution import PlanExecutionCoordinator
from app.plans.models import PlanCreate, PlanStepCreate
from app.plans.service import PlanService
from app.tools.registry import ToolDefinition, ToolRegistry


def build(tmp_path: Path):
    plans = PlanService(tmp_path / "plans.sqlite3")
    tools = ToolRegistry()
    tools.register(ToolDefinition(name="read", description="read", permission="read", handler=lambda value: {"value": value}, parameters={"type": "object"}))
    executor = PlanExecutionCoordinator(plans, tools)
    return plans, tools, AutonomousContinuationService(plans, executor)


def test_deterministic_dependencies_and_durable_restart(tmp_path: Path) -> None:
    _, tools, service = build(tmp_path)
    plan = service.create_goal(PlanCreate(scope="s", workspace="repo", goal="inspect", steps=[PlanStepCreate(title="first"), PlanStepCreate(title="second", metadata={"depends_on_positions": [0]})]))
    assert service.inspect(plan.id, "s")["ready"] == [plan.steps[0].id]
    restarted = AutonomousContinuationService(PlanService(tmp_path / "plans.sqlite3"), PlanExecutionCoordinator(PlanService(tmp_path / "plans.sqlite3"), tools))
    assert restarted.inspect(plan.id, "s")["next"] == plan.steps[0].id


@pytest.mark.parametrize("operation", ["cancel"])
def test_cancellation_is_durable(tmp_path: Path, operation: str) -> None:
    _, _, service = build(tmp_path)
    plan = service.create_goal(PlanCreate(scope="s", goal="stop", steps=[PlanStepCreate(title="pending")]))
    assert getattr(service, operation)(plan.id, "s").status == "cancelled"
    assert service.inspect(plan.id, "s")["plan_status"] == "cancelled"


def test_approval_boundary_pauses_without_execution(tmp_path: Path) -> None:
    plans, tools, service = build(tmp_path)
    tools.register(ToolDefinition(name="write", description="write", permission="filesystem_write", high_impact=True, handler=lambda: "changed", parameters={"type": "object"}))
    plan = service.create_goal(PlanCreate(scope="s", goal="write", steps=[PlanStepCreate(title="write")]))
    auth = tools.authorize("write", "s")
    from app.plans.continuation import StepExecutionSpec
    result = service.continue_once(plan_id=plan.id, scope="s", specifications={plan.steps[0].id: StepExecutionSpec("write", auth, {})})
    assert result["stopped_reason"] == "awaiting_approval"
    assert plans.get(plan.id, "s").steps[0].status == "pending"


def test_bounded_continuation_completes_ready_steps(tmp_path: Path) -> None:
    _, tools, service = build(tmp_path)
    plan = service.create_goal(PlanCreate(scope="s", goal="read", steps=[PlanStepCreate(title="one"), PlanStepCreate(title="two", metadata={"depends_on_positions": [0]})]))
    auth = tools.authorize("read", "s")
    from app.plans.continuation import StepExecutionSpec
    specs = {step.id: StepExecutionSpec("read", auth, {"value": step.title}) for step in plan.steps}
    result = service.continue_once(plan_id=plan.id, scope="s", specifications=specs, bounds=ContinuationBounds(max_steps=2))
    assert result["completed"] == [step.id for step in plan.steps]
    assert result["plan_status"] == "completed"
