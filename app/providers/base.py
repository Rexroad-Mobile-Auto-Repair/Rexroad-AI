from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.providers.models import ModelRequest, ModelResponse, ProviderName, ProviderStreamEvent


class ModelProvider(ABC):
    name: ProviderName

    @abstractmethod
    async def generate(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        raise NotImplementedError

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderStreamEvent]:
        """Fallback adapter: preserve the complete-response contract safely."""
        response = await self.generate(request)
        if response.content:
            yield ProviderStreamEvent(type="text_delta", text=response.content)
        yield ProviderStreamEvent(type="completed", response=response)
