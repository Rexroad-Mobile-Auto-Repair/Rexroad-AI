import httpx
import pytest

from app.providers.models import ModelRequest
from app.providers.openai_compatible import OpenAICompatibleProvider


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
async def test_openai_compatible_generate(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(
        MockTransport(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "hello from local model",
                        }
                    }
                ]
            }
        )
    )

    original_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:1234/v1",
    )

    response = await provider.generate(
        ModelRequest(
            model="qwen3-coder-30b-a3b-instruct",
            messages=[
                {
                    "role": "user",
                    "content": "Hello",
                }
            ],
        )
    )

    assert response.provider == "openai_compatible"
    assert response.model == "qwen3-coder-30b-a3b-instruct"
    assert response.content == "hello from local model"


@pytest.mark.asyncio
async def test_openai_compatible_health_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = httpx.MockTransport(
        MockTransport(
            {
                "data": [],
            }
        )
    )

    original_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:1234/v1",
    )

    assert await provider.health_check() is True


