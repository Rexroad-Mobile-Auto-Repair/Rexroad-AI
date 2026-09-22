from app.providers.models import ModelRequest, ModelResponse
from app.tools.models import ToolCall, ToolSpec


def test_model_request_defaults_to_no_tools() -> None:
    request = ModelRequest(
        model="test-model",
        messages=[
            {
                "role": "user",
                "content": "Hello",
            }
        ],
    )

    assert request.tools == []


def test_model_request_accepts_tool_specs() -> None:
    request = ModelRequest(
        model="test-model",
        messages=[],
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

    assert request.tools[0].name == "git.status"
    assert request.tools[0].parameters["type"] == "object"


def test_model_response_defaults_to_no_tool_calls() -> None:
    response = ModelResponse(
        provider="openai_compatible",
        model="test-model",
        content="Hello",
    )

    assert response.tool_calls == []


def test_model_response_accepts_tool_calls() -> None:
    response = ModelResponse(
        provider="openai_compatible",
        model="test-model",
        tool_calls=[
            ToolCall(
                id="call-1",
                name="git.status",
                arguments={
                    "workspace": "seo_crawler",
                },
            )
        ],
    )

    assert response.content == ""
    assert response.tool_calls[0].id == "call-1"
    assert response.tool_calls[0].name == "git.status"
    assert response.tool_calls[0].arguments == {
        "workspace": "seo_crawler",
    }
