from pathlib import Path

import pytest

from app.plans.models import PlanCreate, PlanStepCreate
from app.plans.service import PlanService


def service(tmp_path: Path) -> PlanService:
    return PlanService(tmp_path / "journal.sqlite3")


def make_plan(service: PlanService, scope: str = "a"):
    return service.create(PlanCreate(scope=scope, goal="ship", steps=[PlanStepCreate(title="first"), PlanStepCreate(title="second")]))


def test_plan_order_next_step_and_transitions(tmp_path: Path) -> None:
    plans = service(tmp_path)
    plan = make_plan(plans)
    assert [step.position for step in plan.steps] == [0, 1]
    assert plans.next_step(plan.id, "a").id == plan.steps[0].id
    plan = plans.transition(plan.id, plan.steps[0].id, "in_progress", "a")
    assert plans.next_step(plan.id, "a").status == "in_progress"
    plan = plans.transition(plan.id, plan.steps[0].id, "completed", "a")
    assert plans.next_step(plan.id, "a").id == plan.steps[1].id
    with pytest.raises(ValueError):
        plans.transition(plan.id, plan.steps[0].id, "failed", "a")
    plans.transition(plan.id, plan.steps[1].id, "skipped", "a")
    assert plans.get(plan.id).status == "completed"
    with pytest.raises(ValueError):
        plans.transition(plan.id, plan.steps[1].id, "in_progress", "a")


def test_plan_scope_restart_and_history(tmp_path: Path) -> None:
    plans = service(tmp_path)
    plan = make_plan(plans)
    other = make_plan(plans, "b")
    assert [item.id for item in plans.list("a")] == [plan.id]
    assert plans.get(plan.id, "b") is None
    plans.transition(plan.id, plan.steps[0].id, "in_progress", "a", "session-1")
    plans.transition(plan.id, plan.steps[0].id, "completed", "a", "action-1")
    restarted = service(tmp_path)
    saved = restarted.get(plan.id, "a")
    assert saved.steps[0].status == "completed"
    assert saved.steps[0].reference == "action-1"
    assert restarted.get("missing") is None
    assert other.id != plan.id


def test_plan_completion_failure_and_deterministic_serialization(tmp_path: Path) -> None:
    plans = service(tmp_path)
    plan = make_plan(plans)
    plans.transition(plan.id, plan.steps[0].id, "skipped", "a")
    plans.transition(plan.id, plan.steps[1].id, "in_progress", "a")
    failed = plans.transition(plan.id, plan.steps[1].id, "failed", "a")
    assert failed.status == "failed"
    assert failed.model_dump_json() == plans.get(plan.id, "a").model_dump_json()
    with pytest.raises(ValueError):
        plans.transition(plan.id, plan.steps[0].id, "in_progress", "a")


def test_cancelled_plan_rejects_step_mutation(tmp_path: Path) -> None:
    plans = service(tmp_path)
    plan = make_plan(plans)
    cancelled = plans.cancel(plan.id, "a")
    assert cancelled.status == "cancelled"
    with pytest.raises(ValueError):
        plans.transition(plan.id, plan.steps[0].id, "in_progress", "a")
