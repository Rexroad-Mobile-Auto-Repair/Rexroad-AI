from abc import ABC, abstractmethod

from app.providers.models import ModelRequest, ModelResponse, ProviderName


class ModelProvider(ABC):
    name: ProviderName

    @abstractmethod
    async def generate(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        raise NotImplementedError
