from pathlib import Path

import pytest

from app.plans.models import PlanCreate, PlanStepCreate
from app.plans.service import PlanService
from app.plans.specs import ExecutionSpecService
from app.tools.registry import ToolDefinition, ToolRegistry


def setup(tmp_path: Path):
    plans = PlanService(tmp_path / "state.sqlite3")
    plan = plans.create(PlanCreate(scope="s", goal="work", steps=[PlanStepCreate(title="one")]))
    tools = ToolRegistry()
    tools.register(ToolDefinition(name="read", description="read", permission="read", parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False}, handler=lambda value: value))
    return plans, plan, tools, ExecutionSpecService(tmp_path / "state.sqlite3", plans, tools)


def test_spec_is_explicit_validated_scoped_and_persisted(tmp_path):
    plans, plan, tools, service = setup(tmp_path)
    spec = service.create(scope="s", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="read", arguments={"value": "x"}, verification={"type": "result_present"})
    assert service.mark_ready(spec.id, "s").status == "ready"
    assert service.get(spec.id, "other") is None
    assert service.get(spec.id, "s").arguments == {"value": "x"}
    restarted = ExecutionSpecService(tmp_path / "state.sqlite3", plans, tools)
    assert restarted.get(spec.id, "s").status == "ready"


def test_invalid_tool_args_and_stale_step_rejected(tmp_path):
    plans, plan, _, service = setup(tmp_path)
    with pytest.raises((KeyError, ValueError)):
        service.create(scope="s", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="missing", arguments={}, verification={})
    with pytest.raises(ValueError):
        service.create(scope="s", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="read", arguments={}, verification={})
    spec = service.create(scope="s", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="read", arguments={"value": "x"}, verification={})
    plans.transition(plan.id, plan.steps[0].id, "in_progress", "s")
    with pytest.raises(ValueError, match="stale"):
        service.mark_ready(spec.id, "s")


def test_ready_spec_materializes_policy_without_executing_or_persisting_capabilities(tmp_path):
    _, plan, tools, service = setup(tmp_path)
    spec = service.create(scope="s", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="read", arguments={"value": "x"}, verification={"type": "text_contains", "text": "x"})
    service.mark_ready(spec.id, "s")
    auth = tools.authorize("read", "s")
    runtime = service.materialize(spec.id, "s", auth)
    assert runtime.tool_name == "read"
    assert runtime.authorization == auth
    assert runtime.verification_policy.verify("x").passed
    assert "token" not in service.get(spec.id, "s").__dict__


def test_materialization_rejects_nonready_and_unknown_policies(tmp_path):
    _, plan, tools, service = setup(tmp_path)
    auth = tools.authorize("read", "s")
    draft = service.create(scope="s", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="read", arguments={"value": "x"}, verification={"type": "result_present"})
    with pytest.raises(ValueError, match="not ready"):
        service.materialize(draft.id, "s", auth)
    bad = service.create(scope="s", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="read", arguments={"value": "x"}, verification={"type": "unknown"})
    service.mark_ready(bad.id, "s")
    with pytest.raises(ValueError, match="verification"):
        service.materialize(bad.id, "s", auth)
