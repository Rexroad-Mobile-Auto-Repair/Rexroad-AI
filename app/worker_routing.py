from pydantic import BaseModel

from app.config import Settings
from app.providers.factory import get_default_model
from app.providers.registry import ProviderRegistry

class WorkerRoute(BaseModel):
    provider: str
    model: str
    reason: str

class WorkerModelRouter:
    def __init__(self, settings: Settings, providers: ProviderRegistry) -> None:
        self._settings, self._providers = settings, providers

    def resolve(self, profile: str) -> WorkerRoute:
        if profile not in {"researcher", "code_analyst", "verifier"}:
            raise ValueError("unknown worker profile")
        provider = self._settings.default_provider
        if provider not in self._providers.names():
            raise ValueError("provider unavailable")
        return WorkerRoute(provider=provider, model=get_default_model(self._settings, provider), reason="configured_default")
