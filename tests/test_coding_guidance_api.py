from fastapi.testclient import TestClient

from app import main


def test_guidance_missing_workflow_is_safe_404() -> None:
    response = TestClient(main.app).get("/supervisor-coding-workflows/missing/guidance", params={"scope": "scope"})
    assert response.status_code == 404
