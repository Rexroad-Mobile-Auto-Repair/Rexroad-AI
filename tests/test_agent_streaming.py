import asyncio

import pytest

from app.agents.models import AgentQueryRequest
from app.agents.service import AgentService
from app.config import Settings
from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse, ProviderStreamEvent
from app.providers.registry import ProviderRegistry


class GatedProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(self) -> None:
        self.gate = asyncio.Event()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(provider=self.name, model=request.model, content="fallback")

    async def stream(self, request: ModelRequest):
        yield ProviderStreamEvent(type="text_delta", text="A")
        await self.gate.wait()
        yield ProviderStreamEvent(type="text_delta", text="B")
        yield ProviderStreamEvent(type="completed", response=ModelResponse(provider=self.name, model=request.model, content="AB"))

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_agent_forwards_text_delta_before_provider_completion():
    provider = GatedProvider()
    registry = ProviderRegistry()
    registry.register(provider)
    stream = AgentService(Settings(_env_file=None), registry).query_stream(AgentQueryRequest(message="hello"))

    first = await anext(stream)
    assert first["type"] == "session"
    delta = await anext(stream)
    assert delta == {"type": "text_delta", "text": "A"}
    provider.gate.set()
    assert (await anext(stream))["text"] == "B"
    assert (await anext(stream))["type"] == "completed"
