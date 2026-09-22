import pytest

from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse
from app.tools.models import ModelMessage


class FakeProvider(ModelProvider):
    name = "ollama"

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            provider=self.name,
            model=request.model,
            content="test response",
        )

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_provider_contract() -> None:
    provider = FakeProvider()

    request = ModelRequest(
        model="test-model",
        messages=[
            ModelMessage(
                role="user",
                content="Hello",
            )
        ],
    )

    response = await provider.generate(request)

    assert response.provider == "ollama"
    assert response.model == "test-model"
    assert response.content == "test response"
    assert await provider.health_check() is True



