import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.providers.models import ModelRequest
from app.providers.openai_provider import OpenAIProvider
from app.tools.models import ModelMessage, ToolCall, ToolSpec


@pytest.mark.asyncio
async def test_openai_generate() -> None:
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    output_text="hello from openai",
                    output=[],
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
    assert response.tool_calls == []

    client.responses.create.assert_awaited_once_with(
        model="test-model",
        input=[
            {
                "role": "user",
                "content": "Hello",
            }
        ],
        temperature=0.0,
    )


@pytest.mark.asyncio
async def test_openai_sends_tools_and_parses_tool_call() -> None:
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    output_text="",
                    output=[
                        SimpleNamespace(
                            type="function_call",
                            call_id="call-1",
                            name="git__status",
                            arguments=json.dumps(
                                {
                                    "workspace": "seo_crawler",
                                }
                            ),
                        )
                    ],
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

    response = await provider.generate(
        ModelRequest(
            model="test-model",
            messages=[
                ModelMessage(
                    role="user",
                    content="Check Git.",
                )
            ],
            tools=[
                ToolSpec(
                    name="git.status",
                    description="Read Git status.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "workspace": {
                                "type": "string",
                            }
                        },
                        "required": ["workspace"],
                    },
                )
            ],
        )
    )

    assert response.tool_calls == [
        ToolCall(
            id="call-1",
            name="git.status",
            arguments={
                "workspace": "seo_crawler",
            },
        )
    ]

    call = client.responses.create.await_args.kwargs

    assert call["tools"][0]["name"] == "git__status"
    assert call["tools"][0]["type"] == "function"


@pytest.mark.asyncio
async def test_openai_sends_tool_result() -> None:
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    output_text="Repository is clean.",
                    output=[],
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

    await provider.generate(
        ModelRequest(
            model="test-model",
            messages=[
                ModelMessage(
                    role="assistant",
                    tool_calls=[
                        ToolCall(
                            id="call-1",
                            name="git.status",
                            arguments={
                                "workspace": "seo_crawler",
                            },
                        )
                    ],
                ),
                ModelMessage(
                    role="tool",
                    content="## main",
                    tool_call_id="call-1",
                    tool_name="git.status",
                ),
            ],
        )
    )

    call = client.responses.create.await_args.kwargs

    assert call["input"] == [
        {
            "type": "function_call",
            "call_id": "call-1",
            "name": "git__status",
            "arguments": '{"workspace": "seo_crawler"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": "## main",
        },
    ]


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
            list=AsyncMock(
                side_effect=RuntimeError("unavailable")
            ),
        ),
    )

    provider = OpenAIProvider(
        api_key="test-key",
        client=client,  # type: ignore[arg-type]
    )

    assert await provider.health_check() is False
