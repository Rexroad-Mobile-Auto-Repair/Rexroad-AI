from app.config import Settings
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import ModelProvider
from app.providers.ollama import OllamaProvider
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.openai_provider import OpenAIProvider
from app.providers.registry import ProviderRegistry


def build_provider_registry(settings: Settings) -> ProviderRegistry:
    registry = ProviderRegistry()

    if settings.openai_api_key:
        registry.register(
            OpenAIProvider(
                api_key=settings.openai_api_key,
            )
        )

    if settings.anthropic_api_key:
        registry.register(
            AnthropicProvider(
                api_key=settings.anthropic_api_key,
            )
        )

    registry.register(
        OpenAICompatibleProvider(
            base_url=settings.local_openai_base_url,
            api_key=settings.local_openai_api_key,
        )
    )

    registry.register(
        OllamaProvider(
            base_url=settings.ollama_base_url,
        )
    )

    return registry


def get_default_model(settings: Settings, provider_name: str) -> str:
    models = {
        "openai": settings.openai_model,
        "anthropic": settings.anthropic_model,
        "openai_compatible": settings.local_openai_model,
        "ollama": settings.ollama_model,
    }

    try:
        return models[provider_name]
    except KeyError as exc:
        raise ValueError(f"Unknown provider: {provider_name}") from exc


def get_default_provider(
    settings: Settings,
    registry: ProviderRegistry,
) -> ModelProvider:
    return registry.get(settings.default_provider)  # type: ignore[arg-type]
