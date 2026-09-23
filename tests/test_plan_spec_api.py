from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.plans.service import PlanService
from app.plans.specs import ExecutionSpecService
from app.tools.registry import ToolDefinition, ToolRegistry


def test_execution_spec_api_lifecycle_and_scope(tmp_path: Path, monkeypatch):
    plans = PlanService(tmp_path / "state.sqlite3")
    tools = ToolRegistry()
    tools.register(ToolDefinition(name="read", description="read", permission="read", parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False}, handler=lambda value: value))
    from app.plans.models import PlanCreate, PlanStepCreate
    plan = plans.create(PlanCreate(scope="a", goal="x", steps=[PlanStepCreate(title="one")]))
    monkeypatch.setattr(main, "plan_service", plans)
    monkeypatch.setattr(main, "tool_registry", tools)
    monkeypatch.setattr(main, "execution_spec_service", ExecutionSpecService(tmp_path / "state.sqlite3", plans, tools))
    client = TestClient(main.app)
    response = client.post("/execution-specs", json={"scope": "a", "plan_id": plan.id, "step_id": plan.steps[0].id, "tool_name": "read", "arguments": {"value": "x"}, "verification": {"type": "result_present"}})
    assert response.status_code == 200
    spec_id = response.json()["id"]
    assert response.json()["status"] == "draft"
    assert client.get("/execution-specs", params={"scope": "b"}).json() == []
    assert client.get(f"/execution-specs/{spec_id}", params={"scope": "b"}).status_code == 404
    assert client.post(f"/execution-specs/{spec_id}/ready", params={"scope": "a"}).json()["status"] == "ready"
    assert client.post(f"/execution-specs/{spec_id}/invalidate", params={"scope": "a"}).json()["status"] == "invalidated"
