import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.agents.models import AgentQueryRequest, AgentQueryResponse


class FakeAgentService:
    async def query(
        self,
        request: AgentQueryRequest,
    ) -> AgentQueryResponse:
        return AgentQueryResponse(
            provider="openai_compatible",
            model="qwen3-coder-30b-a3b-instruct",
            session_id="session-test",
            content=f"response to: {request.message}",
        )


def test_offline_provider_has_actionable_error_without_leaking_details(monkeypatch):
    class Offline:
        async def query(self, request):
            raise httpx.ConnectError("private upstream details")
    monkeypatch.setattr(main_module, "agent_service", Offline())
    response = TestClient(main_module.app).post("/agent/query", json={"message": "Hello"})
    assert response.status_code == 503
    assert "Start-RexroadAI.ps1" in response.json()["detail"]
    assert "private upstream" not in response.text


def test_agent_query_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "agent_service",
        FakeAgentService(),
    )

    client = TestClient(main_module.app)

    response = client.post(
        "/agent/query",
        json={
            "message": "Hello Rexroad AI",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "provider": "openai_compatible",
        "model": "qwen3-coder-30b-a3b-instruct",
        "session_id": "session-test",
        "content": "response to: Hello Rexroad AI",
    }


@pytest.mark.parametrize("reason,expected", [
    ("structured output validation failed: valid JSON object required", "valid bounded proposal"),
    ("proposal must quote unique text from reviewed source", "match the saved source exactly"),
    ("target is pre-existing dirty", "unchanged target files"),
])
def test_proposal_generation_explains_safe_recovery(monkeypatch, reason, expected):
    class Rejected:
        async def generate(self, *args):
            raise ValueError(reason)
    monkeypatch.setattr(main_module, "coding_proposal_service", Rejected())
    response = TestClient(main_module.app).post("/supervisor-coding-workflows/job/proposal/generate?scope=test")
    assert response.status_code == 409
    assert expected in response.json()["detail"]
