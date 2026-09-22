import pytest

from app.agents.models import AgentQueryRequest
from app.agents.service import AgentService
from app.config import Settings
from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse
from app.providers.registry import ProviderRegistry


class FakeProvider(ModelProvider):
    name = "openai_compatible"

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            provider=self.name,
            model=request.model,
            content="agent response",
        )

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_agent_query_uses_default_provider_and_model() -> None:
    settings = Settings(_env_file=None)
    registry = ProviderRegistry()
    registry.register(FakeProvider())

    service = AgentService(settings, registry)

    response = await service.query(
        AgentQueryRequest(
            message="Hello",
        )
    )

    assert response.provider == "openai_compatible"
    assert response.model == "qwen3-coder-30b-a3b-instruct"
    assert response.content == "agent response"
