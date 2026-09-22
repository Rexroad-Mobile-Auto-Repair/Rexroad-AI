import httpx
import pytest

from app.providers.models import ModelRequest
from app.providers.ollama import OllamaProvider


class MockTransport:
    def __init__(self, response_data: dict) -> None:
        self.response_data = response_data

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=self.response_data,
            request=request,
        )


@pytest.mark.asyncio
async def test_ollama_generate(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(
        MockTransport(
            {
                "message": {
                    "role": "assistant",
                    "content": "hello from ollama",
                }
            }
        )
    )

    original_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    provider = OllamaProvider()

    response = await provider.generate(
        ModelRequest(
            model="qwen2.5-coder:14b",
            messages=[
                {
                    "role": "user",
                    "content": "Hello",
                }
            ],
        )
    )

    assert response.provider == "ollama"
    assert response.model == "qwen2.5-coder:14b"
    assert response.content == "hello from ollama"


@pytest.mark.asyncio
async def test_ollama_health_check(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(
        MockTransport(
            {
                "models": [],
            }
        )
    )

    original_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    provider = OllamaProvider()

    assert await provider.health_check() is True


