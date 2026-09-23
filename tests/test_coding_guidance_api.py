from fastapi.testclient import TestClient

from app import main


def test_guidance_missing_workflow_is_safe_404() -> None:
    response = TestClient(main.app).get("/supervisor-coding-workflows/missing/guidance", params={"scope": "scope"})
    assert response.status_code == 404


def test_guidance_endpoint_is_read_only_and_never_calls_provider(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(main, "provider_registry", type("Providers", (), {"get": lambda self, name: calls.append(name)})())
    client = TestClient(main.app)
    before = client.get("/supervisor-coding-workflows/missing/guidance", params={"scope": "scope"}).status_code
    after = client.get("/supervisor-coding-workflows/missing/guidance", params={"scope": "scope"}).status_code
    assert before == after == 404
    assert calls == []
