from fastapi.testclient import TestClient

from app import main
from app.agents.models import AgentQueryResponse


def test_chat_page_loads_without_workspace_and_links_operator():
    response = TestClient(main.app).get("/chat")
    assert response.status_code == 200
    assert "Rexroad AI Chat" in response.text
    assert "Project context" in response.text
    assert 'value="">None' in response.text
    assert 'href="/operator"' in response.text
    assert "api_key" not in response.text.lower()
    assert "ToolAuthorization" not in response.text


def test_root_operator_and_chat_routes_remain_available():
    client = TestClient(main.app)
    assert client.get("/chat").status_code == 200
    assert client.get("/operator").status_code == 200


def test_agent_query_accepts_optional_workspace_without_client_capability(monkeypatch):
    class FakeAgent:
        async def query(self, request):
            assert request.message == "Hello"
            assert request.workspace == "acceptance_test"
            return AgentQueryResponse(provider="openai_compatible", model="test", session_id="s1", content="Hi")

    monkeypatch.setattr(main, "agent_service", FakeAgent())
    response = TestClient(main.app).post("/agent/query", json={"message": "Hello", "workspace": "acceptance_test"})
    assert response.status_code == 200
    assert response.json()["content"] == "Hi"


def test_unknown_chat_workspace_is_rejected(monkeypatch):
    class FakeAgent:
        async def query(self, request):
            raise AssertionError("agent must not be called")

    monkeypatch.setattr(main, "agent_service", FakeAgent())
    response = TestClient(main.app).post("/agent/query", json={"message": "Hello", "workspace": "unknown"})
    assert response.status_code == 404
