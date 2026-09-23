from pathlib import Path

import pytest

from app.plans.continuation import PlanContinuationCoordinator, StepExecutionSpec
from app.plans.execution import PlanExecutionCoordinator
from app.plans.models import PlanCreate, PlanStepCreate
from app.plans.service import PlanService
from app.plans.verification import ResultPresentPolicy
from app.tools.registry import ToolDefinition, ToolRegistry


def setup(tmp_path: Path):
    plans = PlanService(tmp_path / "plans.sqlite3")
    plan = plans.create(PlanCreate(scope="s", goal="two", steps=[PlanStepCreate(title="one"), PlanStepCreate(title="two")]))
    tools = ToolRegistry()
    calls: list[str] = []
    tools.register(ToolDefinition(name="read", description="read", permission="read", handler=lambda value: calls.append(value) or value))
    executor = PlanExecutionCoordinator(plans, tools)
    return plans, plan, tools, calls, PlanContinuationCoordinator(plans, executor)


def spec(tools, value):
    return StepExecutionSpec("read", tools.authorize("read", "s"), {"value": value}, verification_policy=ResultPresentPolicy())


def test_continuation_completes_verified_steps_in_order(tmp_path):
    _, plan, tools, calls, coordinator = setup(tmp_path)
    result = coordinator.continue_plan(scope="s", plan_id=plan.id, max_steps=2, specifications={plan.steps[0].id: spec(tools, "one"), plan.steps[1].id: spec(tools, "two")})
    assert result["completed"] == [plan.steps[0].id, plan.steps[1].id]
    assert result["plan_status"] == "completed"
    assert calls == ["one", "two"]


def test_continuation_bound_and_stops_on_failure(tmp_path):
    _, plan, tools, calls, coordinator = setup(tmp_path)
    result = coordinator.continue_plan(scope="s", plan_id=plan.id, max_steps=1, specifications={plan.steps[0].id: spec(tools, "one"), plan.steps[1].id: spec(tools, "two")})
    assert result["completed"] == [plan.steps[0].id]
    assert calls == ["one"]
    with pytest.raises(ValueError):
        coordinator.continue_plan(scope="s", plan_id=plan.id, max_steps=11, specifications={})
