from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.providers.models import ModelRequest
from app.providers.openai_provider import OpenAIProvider
from app.tools.models import ModelMessage


@pytest.mark.asyncio
async def test_openai_generate() -> None:
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    output_text="hello from openai",
                )
            )
        ),
        models=SimpleNamespace(
            list=AsyncMock(),
        ),
    )

    provider = OpenAIProvider(
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

    assert response.provider == "openai"
    assert response.model == "test-model"
    assert response.content == "hello from openai"

    client.responses.create.assert_awaited_once_with(
        model="test-model",
        input=request.messages,
        temperature=0.0,
    )


@pytest.mark.asyncio
async def test_openai_health_check_success() -> None:
    client = SimpleNamespace(
        responses=SimpleNamespace(),
        models=SimpleNamespace(
            list=AsyncMock(return_value=[]),
        ),
    )

    provider = OpenAIProvider(
        api_key="test-key",
        client=client,  # type: ignore[arg-type]
    )

    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_openai_health_check_failure() -> None:
    client = SimpleNamespace(
        responses=SimpleNamespace(),
        models=SimpleNamespace(
            list=AsyncMock(side_effect=RuntimeError("unavailable")),
        ),
    )

    provider = OpenAIProvider(
        api_key="test-key",
        client=client,  # type: ignore[arg-type]
    )

    assert await provider.health_check() is False



