import pytest

from app.agents.models import AgentQueryRequest
from app.agents.service import (
    AGENT_WORKFLOW_PROMPT,
    VERIFICATION_PROMPT,
    AgentService,
)
from app.config import Settings
from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse
from app.providers.registry import ProviderRegistry
from app.tools.models import ToolCall
from app.tools.registry import ToolDefinition, ToolRegistry


class VerifyingProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(
        self,
        request: ModelRequest,
    ) -> ModelResponse:
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

        if len(self.requests) == 2:
            return ModelResponse(
                provider=self.name,
                model=request.model,
                content="Draft answer.",
            )

        return ModelResponse(
            provider=self.name,
            model=request.model,
            content="Verified final answer.",
        )

    async def health_check(self) -> bool:
        return True


class PlainProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(
        self,
        request: ModelRequest,
    ) -> ModelResponse:
        self.requests.append(request)

        return ModelResponse(
            provider=self.name,
            model=request.model,
            content="Plain answer.",
        )

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_tool_answer_requires_verification_pass() -> None:
    settings = Settings(_env_file=None)

    provider = VerifyingProvider()
    providers = ProviderRegistry()
    providers.register(provider)

    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            name="git.status",
            description="Read Git status.",
            permission="read",
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

    assert response.content == "Verified final answer."
    assert len(provider.requests) == 3

    first_request = provider.requests[0]
    verification_request = provider.requests[2]
    assert verification_request.tools == []

    assert first_request.messages[0].role == "system"
    assert (
        first_request.messages[0].content
        == AGENT_WORKFLOW_PROMPT
    )

    assert verification_request.messages[-2].content == "Draft answer."
    assert verification_request.messages[-1].role == "user"
    assert (
        verification_request.messages[-1].content
        == VERIFICATION_PROMPT
    )


@pytest.mark.asyncio
async def test_plain_answer_does_not_require_verification() -> None:
    settings = Settings(_env_file=None)

    provider = PlainProvider()
    providers = ProviderRegistry()
    providers.register(provider)

    service = AgentService(
        settings,
        providers,
    )

    response = await service.query(
        AgentQueryRequest(
            message="Say hello.",
        )
    )

    assert response.content == "Plain answer."
    assert len(provider.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict,approved", [
    ("VERIFIED", True),
    ("All claims are supported.\n\nVERIFIED", True),
    ("VERIFIED\n\nThe draft correctly explains the source.", True),
    ("Corrected answer.", False),
    ("VERIFIED but the draft needs correction.", False),
    ("The word VERIFIED is part of this corrected answer.", False),
])
async def test_verification_preserves_approved_draft_or_returns_correction(tmp_path, verdict: str, approved: bool) -> None:
    from app.journal.store import ActionJournal
    from app.providers.models import ProviderStreamEvent

    draft = "Source-grounded answer. " * 250
    class VerdictProvider(VerifyingProvider):
        async def generate(self, request):
            response = await super().generate(request)
            if len(self.requests) == 2:
                response.content = draft
            if len(self.requests) == 3:
                response.content = verdict
            return response
        async def stream(self, request):
            response = await self.generate(request)
            if response.content:
                yield ProviderStreamEvent(type="text_delta", text=response.content)
            yield ProviderStreamEvent(type="completed", response=response)

    provider = VerdictProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    tools = ToolRegistry()
    tools.register(ToolDefinition(name="git.status", description="Read Git status.", permission="read", handler=lambda workspace: "## main"))
    journal = ActionJournal(tmp_path / "journal.sqlite3")
    service = AgentService(Settings(_env_file=None), providers, tools=tools, journal=journal)
    events = [event async for event in service.query_stream(AgentQueryRequest(message="Check repository.", workspace="repo"))]
    expected = draft if approved else verdict
    completed = next(event for event in events if event["type"] == "completed")
    assert completed["response"] == expected
    assert [e["text"] for e in events if e["type"] == "text_delta"] == [draft]
    persisted = journal.list_events_for_session(completed["session_id"])
    assert next(e for e in persisted if e.event_type == "verification_response").payload["content"] == verdict
    assert persisted[-1].event_type == "final_response"
    assert persisted[-1].payload["content"] == expected
    assert provider.requests[-1].tools == []
