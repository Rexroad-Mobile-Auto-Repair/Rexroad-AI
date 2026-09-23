from pathlib import Path

import pytest

from app.agents.models import AgentQueryRequest
from app.agents.service import AGENT_WORKFLOW_PROMPT, AgentLoopLimitError, AgentService
from app.config import Settings
from app.journal.store import ActionJournal
from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse
from app.providers.registry import ProviderRegistry
from app.tools.models import ToolCall
from app.tools.registry import ToolDefinition, ToolRegistry


class LifecycleProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(self, fail_at: int | None = None) -> None:
        self.calls = 0
        self.fail_at = fail_at

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.fail_at == self.calls:
            raise OSError("provider-secret")
        if self.calls == 1:
            return ModelResponse(
                provider=self.name,
                model=request.model,
                content="draft",
                tool_calls=[ToolCall(id="call-1", name="read", arguments={})],
            )
        return ModelResponse(provider=self.name, model=request.model, content="final")

    async def health_check(self) -> bool:
        return True


class MultiToolProvider(LifecycleProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                provider=self.name,
                model=request.model,
                content="draft",
                tool_calls=[
                    ToolCall(id="call-a", name="read", arguments={}),
                    ToolCall(id="call-b", name="read", arguments={}),
                ],
            )
        return ModelResponse(provider=self.name, model=request.model, content="final")


class LoopingToolProvider(LifecycleProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            provider=self.name,
            model=request.model,
            tool_calls=[ToolCall(id=f"loop-{self.calls}", name="read", arguments={})],
        )


class HistoryProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(provider=self.name, model=request.model, content=f"reply-{len(self.requests)}")

    async def health_check(self) -> bool:
        return True


def build_service(tmp_path: Path, provider: LifecycleProvider) -> tuple[AgentService, ActionJournal]:
    registry = ProviderRegistry()
    registry.register(provider)
    tools = ToolRegistry()
    tools.register(ToolDefinition(name="read", description="Read", permission="read", handler=lambda: "safe-result"))
    journal = ActionJournal(tmp_path / "journal.sqlite3")
    return AgentService(Settings(_env_file=None), registry, tools=tools, journal=journal), journal


def get_events(journal: ActionJournal):
    with journal._connect() as connection:
        session_id = connection.execute("SELECT session_id FROM agent_events LIMIT 1").fetchone()["session_id"]
    return journal.list_events_for_session(session_id)


@pytest.mark.asyncio
async def test_successful_lifecycle_persists_ordered_events_and_correlations(tmp_path: Path) -> None:
    service, journal = build_service(tmp_path, LifecycleProvider())
    response = await service.query(AgentQueryRequest(message="check"))
    events = journal.list_events_for_session(response.session_id)

    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [event.event_type for event in events] == [
        "user_request", "model_response", "tool_call", "tool_result",
        "model_response", "verification_request", "model_response",
        "verification_response", "final_response",
    ]
    tool_call = next(event for event in events if event.event_type == "tool_call")
    tool_result = next(event for event in events if event.event_type == "tool_result")
    assert tool_call.tool_call_id == "call-1"
    assert tool_result.tool_call_id == "call-1"
    assert tool_result.action_id is not None
    assert events[-1].event_type == "final_response"
    assert events[0].payload["content"] == "check"
    assert all(len(str(event.payload)) <= 2200 for event in events)


@pytest.mark.asyncio
async def test_provider_failure_persists_error_without_final_response(tmp_path: Path) -> None:
    service, journal = build_service(tmp_path, LifecycleProvider(fail_at=1))

    with pytest.raises(OSError):
        await service.query(AgentQueryRequest(message="check"))

    with journal._connect() as connection:
        session_id = connection.execute("SELECT session_id FROM agent_events LIMIT 1").fetchone()["session_id"]
    events = journal.list_events_for_session(session_id)
    assert events[-1].event_type == "error"
    assert all(event.event_type != "final_response" for event in events)
    assert "provider-secret" not in str(events)


@pytest.mark.asyncio
async def test_multiple_tool_calls_preserve_order_and_action_correlations(tmp_path: Path) -> None:
    service, journal = build_service(tmp_path, MultiToolProvider())

    await service.query(AgentQueryRequest(message="check twice"))
    events = get_events(journal)
    tool_events = [event for event in events if event.event_type in {"tool_call", "tool_result"}]

    assert [event.event_type for event in tool_events] == ["tool_call", "tool_result", "tool_call", "tool_result"]
    assert [event.tool_call_id for event in tool_events] == ["call-a", "call-a", "call-b", "call-b"]
    assert tool_events[1].action_id is not None
    assert tool_events[3].action_id is not None
    assert tool_events[1].action_id != tool_events[3].action_id


@pytest.mark.asyncio
async def test_tool_failure_persists_safe_correlated_error_events(tmp_path: Path) -> None:
    provider = LifecycleProvider()
    registry = ProviderRegistry()
    registry.register(provider)
    tools = ToolRegistry()
    tools.register(ToolDefinition(
        name="read", description="Read", permission="read",
        handler=lambda: (_ for _ in ()).throw(RuntimeError("sentinel-secret")),
    ))
    journal = ActionJournal(tmp_path / "journal.sqlite3")
    service = AgentService(Settings(_env_file=None), registry, tools=tools, journal=journal)

    with pytest.raises(RuntimeError, match="sentinel-secret"):
        await service.query(AgentQueryRequest(message="fail"))

    events = get_events(journal)
    tool_events = [event for event in events if event.event_type in {"tool_call", "tool_result", "error"}]
    assert [event.event_type for event in tool_events] == ["tool_call", "tool_result", "error"]
    assert tool_events[1].payload == {"status": "error"}
    assert tool_events[1].action_id is not None
    assert tool_events[2].payload == {"stage": "tool"}
    assert tool_events[0].tool_call_id == tool_events[1].tool_call_id == tool_events[2].tool_call_id
    assert all(event.event_type != "final_response" for event in events)
    assert "sentinel-secret" not in str(events)


@pytest.mark.asyncio
async def test_verification_failure_has_no_fabricated_completion(tmp_path: Path) -> None:
    service, journal = build_service(tmp_path, LifecycleProvider(fail_at=3))

    with pytest.raises(OSError, match="provider-secret"):
        await service.query(AgentQueryRequest(message="verify"))

    events = get_events(journal)
    assert any(event.event_type == "verification_request" for event in events)
    assert events[-1].event_type == "error"
    assert events[-1].payload == {"stage": "verification"}
    assert all(event.event_type not in {"verification_response", "final_response"} for event in events)
    assert "provider-secret" not in str(events)


@pytest.mark.asyncio
async def test_loop_limit_persists_error_after_legitimate_prior_events(tmp_path: Path) -> None:
    provider = LoopingToolProvider()
    registry = ProviderRegistry()
    registry.register(provider)
    tools = ToolRegistry()
    tools.register(ToolDefinition(name="read", description="Read", permission="read", handler=lambda: "ok"))
    journal = ActionJournal(tmp_path / "journal.sqlite3")
    service = AgentService(Settings(_env_file=None), registry, tools=tools, journal=journal, max_tool_rounds=2)

    with pytest.raises(AgentLoopLimitError):
        await service.query(AgentQueryRequest(message="loop"))

    events = get_events(journal)
    assert events[-1].event_type == "error"
    assert events[-1].payload == {"stage": "loop_limit"}
    assert sum(event.event_type == "tool_result" for event in events) == 2
    assert all(event.event_type != "final_response" for event in events)


@pytest.mark.asyncio
async def test_continuation_reuses_session_and_server_history(tmp_path: Path) -> None:
    provider = HistoryProvider()
    registry = ProviderRegistry()
    registry.register(provider)
    journal = ActionJournal(tmp_path / "journal.sqlite3")
    service = AgentService(Settings(_env_file=None), registry, journal=journal)

    first = await service.query(AgentQueryRequest(message="remember pineapple-47"))
    second = await service.query(AgentQueryRequest(message="what was it?", session_id=first.session_id))

    assert second.session_id == first.session_id
    assert [message.content for message in provider.requests[1].messages] == [
        AGENT_WORKFLOW_PROMPT, "remember pineapple-47", "reply-1", "what was it?"
    ]
    events = journal.list_events_for_session(first.session_id)
    assert [event.event_type for event in events] == [
        "user_request", "model_response", "final_response",
        "user_request", "model_response", "final_response",
    ]


@pytest.mark.asyncio
async def test_invalid_session_and_workspace_switch_fail_safely(tmp_path: Path) -> None:
    provider = HistoryProvider()
    registry = ProviderRegistry()
    registry.register(provider)
    journal = ActionJournal(tmp_path / "journal.sqlite3")
    service = AgentService(Settings(_env_file=None), registry, journal=journal)

    first = await service.query(AgentQueryRequest(message="hello", workspace=None))
    with pytest.raises(ValueError, match="Session not found"):
        await service.query(AgentQueryRequest(message="bad", session_id="missing"))
    with pytest.raises(ValueError, match="Workspace context"):
        await service.query(AgentQueryRequest(message="switch", session_id=first.session_id, workspace="acceptance_test"))


def test_event_payload_content_is_exactly_bounded(tmp_path: Path) -> None:
    journal = ActionJournal(tmp_path / "journal.sqlite3")
    content = "x" * (journal.EVENT_CONTENT_LIMIT + 37)

    event = journal.append_event(session_id="bounded", event_type="user_request", payload={"content": content})
    persisted = journal.list_events_for_session("bounded")[0]

    assert len(persisted.payload["content"]) == journal.EVENT_CONTENT_LIMIT
    assert persisted.payload["content"] == content[: journal.EVENT_CONTENT_LIMIT]
    assert persisted.id == event.id


def test_event_sequence_is_unique_per_session_and_legacy_actions_remain_readable(tmp_path: Path) -> None:
    journal = ActionJournal(tmp_path / "journal.sqlite3")
    journal.record(
        session_id="legacy", provider="fake", model="model", tool="read",
        permission="read", arguments={}, status="success", result_preview="ok",
    )
    journal.append_event(session_id="new", event_type="user_request", payload={"content": "x"})
    journal.append_event(session_id="new", event_type="final_response", payload={"content": "y"})

    assert journal.list_session("legacy")[0].session_id == "legacy"
    assert [event.sequence for event in journal.list_events_for_session("new")] == [1, 2]
