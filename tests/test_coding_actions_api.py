from fastapi.testclient import TestClient

from app import main


def test_action_api_rejects_unknown_action_before_dispatch() -> None:
    client = TestClient(main.app)
    response = client.post("/supervisor-coding-workflows/unknown/action", json={"action": "shell", "scope": "scope"})
    assert response.status_code == 422


def test_action_api_requires_explicit_scope_and_typed_body() -> None:
    client = TestClient(main.app)
    response = client.post("/supervisor-coding-workflows/unknown/action", json={"action": "start_analysis"})
    assert response.status_code == 422
