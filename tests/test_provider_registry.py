import pytest

from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse
from app.providers.registry import ProviderNotRegisteredError, ProviderRegistry


class FakeProvider(ModelProvider):
    def __init__(self, name: str) -> None:
        self.name = name  # type: ignore[assignment]

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            provider=self.name,
            model=request.model,
            content="ok",
        )

    async def health_check(self) -> bool:
        return True


def test_register_and_get_provider() -> None:
    registry = ProviderRegistry()
    provider = FakeProvider("openai")

    registry.register(provider)

    assert registry.get("openai") is provider


def test_registered_provider_names_are_sorted() -> None:
    registry = ProviderRegistry()

    registry.register(FakeProvider("ollama"))
    registry.register(FakeProvider("anthropic"))
    registry.register(FakeProvider("openai"))

    assert registry.names() == [
        "anthropic",
        "ollama",
        "openai",
    ]


def test_unknown_provider_raises() -> None:
    registry = ProviderRegistry()

    with pytest.raises(ProviderNotRegisteredError):
        registry.get("openai")
