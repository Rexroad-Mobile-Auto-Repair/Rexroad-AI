import pytest

from app.plans.execution import PlanExecutionCoordinator, PlanExecutionError
from app.plans.models import PlanCreate, PlanStepCreate
from app.plans.service import PlanService
from app.plans.verification import FieldEqualsPolicy, ResultPresentPolicy, TextContainsPolicy
from app.tools.registry import ToolDefinition, ToolRegistry


def test_result_present_and_text_policies_are_deterministic():
    assert ResultPresentPolicy().verify({}).passed
    assert not ResultPresentPolicy().verify(None).passed
    assert TextContainsPolicy("ready").verify("system ready").passed
    assert not TextContainsPolicy("ready").verify("not yet").passed


def test_field_policy_checks_structured_values():
    policy = FieldEqualsPolicy("status", "ok")
    assert policy.verify({"status": "ok"}).passed
    assert not policy.verify({"status": "bad"}).passed
    assert not policy.verify("ok").passed


def test_malformed_policies_are_rejected():
    with pytest.raises(ValueError):
        FieldEqualsPolicy("", "x")
    with pytest.raises(ValueError):
        TextContainsPolicy("bad\ntext")


def test_policy_runs_before_completion_and_failed_verification_does_not_rerun_tool(tmp_path):
    plans = PlanService(tmp_path / "plans.sqlite3")
    plan = plans.create(PlanCreate(scope="s", goal="verify", steps=[PlanStepCreate(title="one")]))
    calls = []
    tools = ToolRegistry()
    tools.register(ToolDefinition(name="read", description="read", permission="read", handler=lambda: calls.append(1) or {"ok": False}))
    coordinator = PlanExecutionCoordinator(plans, tools)
    authorization = tools.authorize("read", "s")
    with pytest.raises(PlanExecutionError, match="verification failed"):
        coordinator.execute_once(scope="s", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="read", authorization=authorization, arguments={}, verification_policy=FieldEqualsPolicy("ok", True))
    assert calls == [1]
    assert plans.get(plan.id, "s").steps[0].status == "failed"
