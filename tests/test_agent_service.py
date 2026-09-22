import pytest

from app.agents.models import AgentQueryRequest
from app.agents.service import AgentLoopLimitError, AgentService
from app.config import Settings
from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse
from app.providers.registry import ProviderRegistry
from app.tools.models import ToolCall
from app.tools.registry import ToolDefinition, ToolRegistry


class FakeProvider(ModelProvider):
    name = "openai_compatible"

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            provider=self.name,
            model=request.model,
            content="agent response",
        )

    async def health_check(self) -> bool:
        return True


class ToolCallingProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)

        if len(self.requests) == 1:
            return ModelResponse(
                provider=self.name,
                model=request.model,
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="git.status",
                        arguments={
                            "workspace": "repo",
                        },
                    )
                ],
            )

        return ModelResponse(
            provider=self.name,
            model=request.model,
            content="The repository is clean.",
        )

    async def health_check(self) -> bool:
        return True


class EndlessToolProvider(ModelProvider):
    name = "openai_compatible"

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            provider=self.name,
            model=request.model,
            tool_calls=[
                ToolCall(
                    id="call-loop",
                    name="git.status",
                    arguments={
                        "workspace": "repo",
                    },
                )
            ],
        )

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_agent_query_uses_default_provider_and_model() -> None:
    settings = Settings(_env_file=None)
    registry = ProviderRegistry()
    registry.register(FakeProvider())

    service = AgentService(settings, registry)

    response = await service.query(
        AgentQueryRequest(
            message="Hello",
        )
    )

    assert response.provider == "openai_compatible"
    assert response.model == "qwen3-coder-30b-a3b-instruct"
    assert response.content == "agent response"


@pytest.mark.asyncio
async def test_agent_executes_requested_tool_and_returns_final_answer() -> None:
    settings = Settings(_env_file=None)

    provider = ToolCallingProvider()
    providers = ProviderRegistry()
    providers.register(provider)

    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            name="git.status",
            description="Read Git status.",
            permission="read",
            parameters={
                "type": "object",
                "properties": {
                    "workspace": {
                        "type": "string",
                    }
                },
                "required": ["workspace"],
            },
            handler=lambda workspace: f"## main ({workspace})",
        )
    )

    service = AgentService(
        settings,
        providers,
        tools=tools,
    )

    response = await service.query(
        AgentQueryRequest(
            message="Check the repository.",
        )
    )

    assert response.content == "The repository is clean."
    assert len(provider.requests) == 2

    second_request = provider.requests[1]

    assert second_request.messages[-1].role == "tool"
    assert second_request.messages[-1].tool_call_id == "call-1"
    assert second_request.messages[-1].tool_name == "git.status"
    assert second_request.messages[-1].content == "## main (repo)"


@pytest.mark.asyncio
async def test_agent_exposes_registered_tool_specs() -> None:
    settings = Settings(_env_file=None)

    provider = ToolCallingProvider()
    providers = ProviderRegistry()
    providers.register(provider)

    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            name="git.status",
            description="Read Git status.",
            permission="read",
            parameters={
                "type": "object",
            },
            handler=lambda workspace: "## main",
        )
    )

    service = AgentService(
        settings,
        providers,
        tools=tools,
    )

    await service.query(
        AgentQueryRequest(
            message="Check Git.",
        )
    )

    assert provider.requests[0].tools[0].name == "git.status"


@pytest.mark.asyncio
async def test_agent_stops_runaway_tool_loop() -> None:
    settings = Settings(_env_file=None)

    providers = ProviderRegistry()
    providers.register(EndlessToolProvider())

    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            name="git.status",
            description="Read Git status.",
            permission="read",
            handler=lambda workspace: "## main",
        )
    )

    service = AgentService(
        settings,
        providers,
        tools=tools,
        max_tool_rounds=2,
    )

    with pytest.raises(
        AgentLoopLimitError,
        match="exceeded 2 rounds",
    ):
        await service.query(
            AgentQueryRequest(
                message="Keep checking forever.",
            )
        )
