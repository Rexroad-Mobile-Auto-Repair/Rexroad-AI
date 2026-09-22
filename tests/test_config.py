import pytest

from app.config import Settings
from app.providers.factory import (
    build_provider_registry,
    get_default_model,
    get_default_provider,
)


def test_default_settings_use_local_provider() -> None:
    settings = Settings(_env_file=None)

    assert settings.default_provider == "openai_compatible"
    assert settings.local_openai_base_url == "http://localhost:1234/v1"
    assert settings.local_openai_model == "qwen3-coder-30b-a3b-instruct"


def test_registry_always_includes_local_providers() -> None:
    settings = Settings(_env_file=None)

    registry = build_provider_registry(settings)

    assert "openai_compatible" in registry.names()
    assert "ollama" in registry.names()
    assert "openai" not in registry.names()
    assert "anthropic" not in registry.names()


def test_registry_includes_cloud_providers_when_keys_exist() -> None:
    settings = Settings(
        _env_file=None,
        openai_api_key="openai-test-key",
        anthropic_api_key="anthropic-test-key",
    )

    registry = build_provider_registry(settings)

    assert registry.names() == [
        "anthropic",
        "ollama",
        "openai",
        "openai_compatible",
    ]


def test_default_model_lookup() -> None:
    settings = Settings(_env_file=None)

    assert (
        get_default_model(settings, "openai_compatible")
        == "qwen3-coder-30b-a3b-instruct"
    )


def test_unknown_default_model_provider_raises() -> None:
    settings = Settings(_env_file=None)

    with pytest.raises(ValueError, match="Unknown provider"):
        get_default_model(settings, "invalid")


def test_get_default_provider() -> None:
    settings = Settings(_env_file=None)
    registry = build_provider_registry(settings)

    provider = get_default_provider(settings, registry)

    assert provider.name == "openai_compatible"
