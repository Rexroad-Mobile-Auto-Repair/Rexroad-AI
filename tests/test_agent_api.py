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
            content=f"response to: {request.message}",
        )


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
        "content": "response to: Hello Rexroad AI",
    }
