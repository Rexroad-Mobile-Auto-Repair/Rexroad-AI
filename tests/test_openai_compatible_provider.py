import json

import httpx
import pytest

from app.providers.models import ModelRequest
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.tools.models import ModelMessage, ToolCall, ToolSpec


class MockTransport:
    def __init__(self, response_data: dict) -> None:
        self.response_data = response_data
        self.last_request: httpx.Request | None = None

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.last_request = request

        return httpx.Response(
            200,
            json=self.response_data,
            request=request,
        )


@pytest.mark.asyncio
async def test_openai_compatible_generate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_handler = MockTransport(
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

    transport = httpx.MockTransport(mock_handler)
    original_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        client_factory,
    )

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:1234/v1",
    )

    response = await provider.generate(
        ModelRequest(
            model="qwen3-coder-30b-a3b-instruct",
            messages=[
                ModelMessage(
                    role="user",
                    content="Hello",
                )
            ],
        )
    )

    assert response.provider == "openai_compatible"
    assert response.model == "qwen3-coder-30b-a3b-instruct"
    assert response.content == "hello from local model"
    assert response.tool_calls == []


@pytest.mark.asyncio
async def test_openai_compatible_sends_tool_specs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_handler = MockTransport(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "git.status",
                                    "arguments": json.dumps(
                                        {
                                            "workspace": "seo_crawler",
                                        }
                                    ),
                                },
                            }
                        ],
                    }
                }
            ]
        }
    )

    transport = httpx.MockTransport(mock_handler)
    original_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        client_factory,
    )

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:1234/v1",
    )

    response = await provider.generate(
        ModelRequest(
            model="qwen3-coder-30b-a3b-instruct",
            messages=[
                ModelMessage(
                    role="user",
                    content="Check Git status.",
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

    assert response.content == ""
    assert response.tool_calls == [
        ToolCall(
            id="call-1",
            name="git.status",
            arguments={
                "workspace": "seo_crawler",
            },
        )
    ]

    assert mock_handler.last_request is not None

    request_payload = json.loads(
        mock_handler.last_request.content
    )

    assert request_payload["tools"][0]["function"]["name"] == "git.status"
    assert (
        request_payload["tools"][0]["function"]["parameters"]["type"]
        == "object"
    )


@pytest.mark.asyncio
async def test_openai_compatible_sends_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_handler = MockTransport(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Repository is clean.",
                    }
                }
            ]
        }
    )

    transport = httpx.MockTransport(mock_handler)
    original_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        client_factory,
    )

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:1234/v1",
    )

    await provider.generate(
        ModelRequest(
            model="qwen3-coder-30b-a3b-instruct",
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

    assert mock_handler.last_request is not None

    request_payload = json.loads(
        mock_handler.last_request.content
    )

    assistant = request_payload["messages"][0]
    tool_result = request_payload["messages"][1]

    assert assistant["tool_calls"][0]["id"] == "call-1"
    assert (
        assistant["tool_calls"][0]["function"]["name"]
        == "git.status"
    )

    assert tool_result == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "## main",
    }


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

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        client_factory,
    )

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:1234/v1",
    )

    assert await provider.health_check() is True
