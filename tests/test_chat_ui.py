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
    assert "session_id.slice" not in response.text
    assert "/sessions/" in response.text
    assert "currentSessionId" in response.text
    assert "session_id:currentSessionId" in response.text


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


def test_workspace_free_request_is_not_given_workspace_context(monkeypatch):
    class FakeAgent:
        async def query(self, request):
            assert request.workspace is None
            return AgentQueryResponse(provider="openai_compatible", model="test", session_id="s2", content="workspace-free")

    monkeypatch.setattr(main, "agent_service", FakeAgent())
    response = TestClient(main.app).post("/agent/query", json={"message": "hi"})
    assert response.status_code == 200
    assert response.json()["content"] == "workspace-free"


def test_unknown_chat_workspace_is_rejected(monkeypatch):
    class FakeAgent:
        async def query(self, request):
            raise AssertionError("agent must not be called")

    monkeypatch.setattr(main, "agent_service", FakeAgent())
    response = TestClient(main.app).post("/agent/query", json={"message": "Hello", "workspace": "unknown"})
    assert response.status_code == 404


def test_session_summary_uses_first_user_message_title(tmp_path):
    from app.journal.store import ActionJournal

    journal = ActionJournal(tmp_path / "journal.sqlite3")
    journal.record(session_id="session-1", provider="test", model="test", tool="", permission="", arguments={}, status="success")
    journal.append_event(session_id="session-1", event_type="user_request", payload={"content": "Inspect the acceptance workspace"})
    assert journal.list_sessions(limit=1)[0].title == "Inspect the acceptance workspace"
