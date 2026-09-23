from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.plans.service import PlanService


def test_scoped_plan_api_lifecycle(tmp_path: Path, monkeypatch) -> None:
    service = PlanService(tmp_path / "plans.sqlite3")
    monkeypatch.setattr(main, "plan_service", service)
    client = TestClient(main.app)
    created = client.post("/plans", json={"scope": "a", "workspace": "repo", "goal": "ship", "steps": [{"title": "one"}, {"title": "two"}]}).json()
    plan_id, step_id = created["id"], created["steps"][0]["id"]
    assert client.get("/plans", params={"scope": "a"}).json()[0]["id"] == plan_id
    assert client.get("/plans", params={"scope": "b"}).json() == []
    assert client.get(f"/plans/{plan_id}", params={"scope": "b"}).status_code == 404
    assert client.get(f"/plans/{plan_id}/next-step", params={"scope": "a"}).json()["id"] == step_id
    updated = client.patch(f"/plans/{plan_id}/steps/{step_id}", params={"scope": "a", "status": "in_progress", "reference": "session-1"})
    assert updated.status_code == 200
    assert updated.json()["steps"][0]["reference"] == "session-1"
    assert client.post(f"/plans/{plan_id}/cancel", params={"scope": "a"}).status_code == 200
    assert client.patch(f"/plans/{plan_id}/steps/{step_id}", params={"scope": "a", "status": "completed"}).status_code == 409


def test_plan_api_validation_and_unknown_ids(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main, "plan_service", PlanService(tmp_path / "plans.sqlite3"))
    client = TestClient(main.app)
    assert client.get("/plans/missing", params={"scope": "a"}).status_code == 404
    assert client.get("/plans/missing/next-step", params={"scope": "a"}).status_code == 404
    response = client.post("/plans", json={"scope": "a", "goal": "", "steps": []})
    assert response.status_code == 422
