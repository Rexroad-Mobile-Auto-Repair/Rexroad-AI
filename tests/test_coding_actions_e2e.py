from fastapi.testclient import TestClient

from app import main


def test_coding_action_boundary_rejects_unknown_action() -> None:
    response = TestClient(main.app).post(
        "/supervisor-coding-workflows/unknown/action",
        json={"action": "execute_shell", "scope": "scope"},
    )
    assert response.status_code == 422


def test_coding_action_boundary_requires_scope() -> None:
    response = TestClient(main.app).post(
        "/supervisor-coding-workflows/unknown/action",
        json={"action": "start_analysis"},
    )
    assert response.status_code == 422
