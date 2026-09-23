from pathlib import Path

import pytest

from app.plans.bridge import TrustedExecutionBridge
from app.plans.models import PlanCreate, PlanStepCreate
from app.plans.service import PlanService
from app.plans.specs import ExecutionSpecService
from app.tools.registry import ToolDefinition, ToolRegistry


def setup(tmp_path: Path, high_impact=False):
    plans = PlanService(tmp_path / "state.sqlite3")
    plan = plans.create(PlanCreate(scope="s", goal="work", steps=[PlanStepCreate(title="one")]))
    tools = ToolRegistry(tmp_path / "state.sqlite3")
    tools.register(ToolDefinition(name="tool", description="tool", permission="read", high_impact=high_impact, parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False}, handler=lambda value: value))
    specs = ExecutionSpecService(tmp_path / "state.sqlite3", plans, tools)
    return plans, plan, tools, specs


def ready_spec(specs, plan):
    spec = specs.create(scope="s", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="tool", arguments={"value": "x"}, verification={"type": "result_present"})
    specs.mark_ready(spec.id, "s")
    return spec


def test_bridge_issues_internal_low_risk_authorization_without_execution(tmp_path):
    _, plan, tools, specs = setup(tmp_path)
    spec = ready_spec(specs, plan)
    runtime, authorization, approval = TrustedExecutionBridge(specs, tools).prepare(scope="s", spec_id=spec.id)
    assert runtime.tool_name == "tool"
    assert tools.validate_authorization(authorization, "tool", "s")
    assert approval is None


def test_bridge_requires_matching_approved_request_and_consumes_once(tmp_path):
    _, plan, tools, specs = setup(tmp_path, high_impact=True)
    spec = ready_spec(specs, plan)
    auth = tools.authorize("tool", "s")
    request = tools.request_approval(auth, {"value": "x"}, "approved action")
    tools.review_approval(request.id, "s", True)
    bridge = TrustedExecutionBridge(specs, tools)
    runtime, _, approval = bridge.prepare(scope="s", spec_id=spec.id, approval_request_id=request.id)
    assert runtime.approval == approval
    assert tools.get_approval_request(request.id, "s").status == "consumed"
    with pytest.raises(ValueError):
        bridge.prepare(scope="s", spec_id=spec.id, approval_request_id=request.id)


def test_bridge_rejects_missing_or_wrong_approval_without_execution(tmp_path):
    _, plan, tools, specs = setup(tmp_path, high_impact=True)
    spec = ready_spec(specs, plan)
    bridge = TrustedExecutionBridge(specs, tools)
    with pytest.raises(ValueError):
        bridge.prepare(scope="s", spec_id=spec.id)
