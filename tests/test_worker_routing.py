import pytest

from app.config import Settings
from app.providers.registry import ProviderRegistry
from app.worker_routing import WorkerModelRouter


def test_profiles_use_configured_default():
    settings = Settings()
    registry = ProviderRegistry()
    class Provider:
        name = "openai_compatible"
    registry.register(Provider())
    router = WorkerModelRouter(settings, registry)
    assert router.resolve("researcher").model == settings.local_openai_model
    assert router.resolve("code_analyst").provider == settings.default_provider
    assert router.resolve("verifier").reason == "configured_default"


def test_unknown_or_unregistered_provider_rejected():
    settings = Settings(default_provider="missing")
    with pytest.raises(ValueError):
        WorkerModelRouter(settings, ProviderRegistry()).resolve("researcher")
    with pytest.raises(ValueError):
        WorkerModelRouter(Settings(), ProviderRegistry()).resolve("other")
