from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.models import ModelRequest
from app.tools.models import ModelMessage


@pytest.mark.asyncio
async def test_anthropic_generate() -> None:
    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="text",
                            text="hello from anthropic",
                        )
                    ]
                )
            )
        ),
        models=SimpleNamespace(
            list=AsyncMock(),
        ),
    )

    provider = AnthropicProvider(
        api_key="test-key",
        client=client,  # type: ignore[arg-type]
    )

    request = ModelRequest(
        model="test-model",
        messages=[
            ModelMessage(
                role="user",
                content="Hello",
            )
        ],
        temperature=0.0,
    )

    response = await provider.generate(request)

    assert response.provider == "anthropic"
    assert response.model == "test-model"
    assert response.content == "hello from anthropic"

    client.messages.create.assert_awaited_once_with(
        model="test-model",
        messages=request.messages,
        temperature=0.0,
        max_tokens=4096,
    )


@pytest.mark.asyncio
async def test_anthropic_health_check_success() -> None:
    client = SimpleNamespace(
        messages=SimpleNamespace(),
        models=SimpleNamespace(
            list=AsyncMock(return_value=[]),
        ),
    )

    provider = AnthropicProvider(
        api_key="test-key",
        client=client,  # type: ignore[arg-type]
    )

    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_anthropic_health_check_failure() -> None:
    client = SimpleNamespace(
        messages=SimpleNamespace(),
        models=SimpleNamespace(
            list=AsyncMock(side_effect=RuntimeError("unavailable")),
        ),
    )

    provider = AnthropicProvider(
        api_key="test-key",
        client=client,  # type: ignore[arg-type]
    )

    assert await provider.health_check() is False



