from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.plans.models import PlanCreate, PlanStepCreate
from app.plans.service import PlanService
from app.plans.specs import ExecutionSpecService
from app.tools.registry import ToolDefinition, ToolRegistry


def test_execution_spec_endpoint_runs_one_low_risk_step(tmp_path: Path, monkeypatch):
    plans = PlanService(tmp_path / "state.sqlite3")
    tools = ToolRegistry(tmp_path / "state.sqlite3")
    calls: list[str] = []
    tools.register(ToolDefinition(name="read", description="read", permission="read", parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False}, handler=lambda value: calls.append(value) or value))
    plan = plans.create(PlanCreate(scope="s", goal="x", steps=[PlanStepCreate(title="one"), PlanStepCreate(title="two")]))
    specs = ExecutionSpecService(tmp_path / "state.sqlite3", plans, tools)
    spec = specs.create(scope="s", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="read", arguments={"value": "x"}, verification={"type": "result_present"})
    specs.mark_ready(spec.id, "s")
    monkeypatch.setattr(main, "execution_spec_service", specs)
    monkeypatch.setattr(main, "execution_bridge", __import__("app.plans.bridge", fromlist=["TrustedExecutionBridge"]).TrustedExecutionBridge(specs, tools))
    monkeypatch.setattr(main, "plan_execution", __import__("app.plans.execution", fromlist=["PlanExecutionCoordinator"]).PlanExecutionCoordinator(plans, tools))
    response = TestClient(main.app).post(f"/execution-specs/{spec.id}/execute", params={"scope": "s"})
    assert response.status_code == 200
    assert calls == ["x"]
    assert plans.get(plan.id, "s").steps[0].status == "completed"
    assert plans.get(plan.id, "s").steps[1].status == "pending"
