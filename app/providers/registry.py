from app.providers.base import ModelProvider
from app.providers.models import ProviderName


class ProviderNotRegisteredError(KeyError):
    pass


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[ProviderName, ModelProvider] = {}

    def register(self, provider: ModelProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: ProviderName) -> ModelProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ProviderNotRegisteredError(name) from exc

    def names(self) -> list[ProviderName]:
        return sorted(self._providers)
