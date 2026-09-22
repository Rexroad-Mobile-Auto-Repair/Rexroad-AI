from fastapi.testclient import TestClient

import app.main as main_module
from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse
from app.providers.registry import ProviderRegistry


class FakeProvider(ModelProvider):
    name = "openai_compatible"

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            provider=self.name,
            model=request.model,
            content="ok",
        )

    async def health_check(self) -> bool:
        return True


def test_providers_endpoint(monkeypatch) -> None:
    registry = ProviderRegistry()
    registry.register(FakeProvider())

    monkeypatch.setattr(main_module, "provider_registry", registry)

    client = TestClient(main_module.app)

    response = client.get("/providers")

    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "openai_compatible",
            "configured": True,
            "healthy": True,
            "model": "qwen3-coder-30b-a3b-instruct",
        }
    ]
