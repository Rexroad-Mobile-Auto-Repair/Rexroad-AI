import json

import pytest

from app.agents.models import AgentQueryRequest
from app.agents.service import AGENT_WORKFLOW_PROMPT, VERIFICATION_PROMPT, AgentService
from app.config import Settings
from app.context.builder import ContextBudgetError, ContextBuilder
from app.context.models import ContextRequest
from app.journal.store import ActionJournal
from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse
from app.providers.registry import ProviderRegistry
from app.tools.models import ModelMessage, ToolCall
from app.tools.registry import ToolDefinition, ToolRegistry


def build_request(messages, total=10000, tool=10000):
    return ContextRequest(
        messages=messages,
        total_byte_budget=total,
        tool_result_byte_budget=tool,
    )


def test_context_is_deterministic_and_counts_utf8_bytes():
    messages = [ModelMessage(role="system", content="é"), ModelMessage(role="user", content="質問")]
    first = ContextBuilder().build(build_request(messages))
    second = ContextBuilder().build(build_request(messages))
    assert first.model_dump() == second.model_dump()
    expected = sum(
        len(
            json.dumps(
                message.model_dump(mode="json", exclude_none=True),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        for message in first.messages
    )
    assert first.byte_count == expected


@pytest.mark.parametrize(
    ("content", "budget", "expected", "truncated"),
    [("abc", 3, "abc", False), ("abc", 2, "ab", True)],
)
def test_ascii_tool_result_boundaries(content, budget, expected, truncated):
    result = ContextBuilder().build(build_request([
        ModelMessage(role="tool", content=content, tool_call_id="call-1", tool_name="read")
    ], tool=budget))
    assert result.messages[0].content == expected
    assert bool(result.truncations) is truncated
    if truncated:
        assert result.truncations[0].original_byte_count == len(content)
        assert result.truncations[0].bounded_byte_count == budget


def test_multibyte_boundary_has_exact_valid_prefix():
    content = "A🙂B"
    result = ContextBuilder().build(build_request([
        ModelMessage(role="tool", content=content, tool_call_id="call-1", tool_name="read")
    ], tool=3))
    assert result.messages[0].content == "A"
    assert result.messages[0].content.encode("utf-8").decode("utf-8") == "A"
    assert result.truncations[0].bounded_byte_count == 1


def test_tool_result_is_bounded_on_utf8_boundary_without_mutating_input():
    original = ModelMessage(role="tool", content="🙂🙂🙂", tool_call_id="call-1", tool_name="knowledge.search")
    result = ContextBuilder().build(build_request([original], tool=5))
    assert result.messages[0].content == "🙂"
    assert len(result.messages[0].content.encode("utf-8")) == 4
    assert result.messages[0].tool_call_id == "call-1"
    assert result.messages[0].tool_name == "knowledge.search"
    assert original.content == "🙂🙂🙂"
    assert result.truncations[0].message_index == 0


def test_bounding_is_copy_isolated_for_structured_multi_message_history():
    messages = [
        ModelMessage(role="system", content="system"),
        ModelMessage(
            role="assistant",
            tool_calls=[ToolCall(id="call-1", name="read", arguments={"path": "x"})],
        ),
        ModelMessage(
            role="tool",
            content="abcdef",
            tool_call_id="call-1",
            tool_name="read",
        ),
    ]
    original = [message.model_copy(deep=True) for message in messages]
    result = ContextBuilder().build(build_request(messages, tool=2))
    assert messages == original
    assert result.messages[1].tool_calls == original[1].tool_calls
    assert result.messages[2].content == "ab"


def test_structured_tool_call_and_order_are_preserved():
    messages = [
        ModelMessage(role="system", content="system"),
        ModelMessage(role="user", content="request"),
        ModelMessage(
            role="assistant",
            tool_calls=[ToolCall(id="call-1", name="read", arguments={"x": 1})],
        ),
        ModelMessage(role="tool", content="answer", tool_call_id="call-1", tool_name="read"),
        ModelMessage(role="assistant", content="draft"),
        ModelMessage(role="user", content="Verify this."),
    ]
    result = ContextBuilder().build(build_request(messages))
    assert [message.role for message in result.messages] == [message.role for message in messages]
    assert result.messages[2].tool_calls[0].id == "call-1"
    assert result.messages[2].tool_calls[0].name == "read"
    assert result.messages[2].tool_calls[0].arguments == {"x": 1}


def test_actual_agent_prompts_are_preserved():
    result = ContextBuilder().build(build_request([
        ModelMessage(role="system", content=AGENT_WORKFLOW_PROMPT),
        ModelMessage(role="user", content="draft"),
        ModelMessage(role="assistant", content="draft answer"),
        ModelMessage(role="user", content=VERIFICATION_PROMPT),
    ]))
    assert result.messages[0].content == AGENT_WORKFLOW_PROMPT
    assert result.messages[-1].content == VERIFICATION_PROMPT


def test_knowledge_search_result_is_ordinary_bounded_tool_message():
    result = ContextBuilder().build(build_request([
        ModelMessage(
            role="tool",
            content="knowledge result",
            tool_call_id="call-knowledge",
            tool_name="knowledge.search",
        )
    ], tool=5))
    assert result.messages[0].content == "knowl"
    assert result.messages[0].tool_name == "knowledge.search"


def test_required_context_over_budget_fails_explicitly():
    with pytest.raises(ContextBudgetError):
        ContextBuilder().build(build_request([ModelMessage(role="system", content="required")], total=1))


def test_total_budget_exact_boundary_succeeds_and_one_byte_less_fails():
    message = ModelMessage(role="user", content="exact")
    encoded = json.dumps(
        message.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert ContextBuilder().build(build_request([message], total=len(encoded))).byte_count == len(encoded)
    with pytest.raises(ContextBudgetError):
        ContextBuilder().build(build_request([message], total=len(encoded) - 1))


class CountingProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(provider=self.name, model=request.model, content="ok")

    async def health_check(self) -> bool:
        return True


class ToolCaptureProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelResponse(
                provider=self.name,
                model=request.model,
                tool_calls=[ToolCall(id="call-1", name="knowledge.search", arguments={})],
            )
        return ModelResponse(provider=self.name, model=request.model, content="done")

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_agent_provider_request_bounds_tool_result_without_mutating_history():
    provider = ToolCaptureProvider()
    registry = ProviderRegistry()
    registry.register(provider)
    tools = ToolRegistry()
    tools.register(ToolDefinition(
        name="knowledge.search",
        description="Search",
        permission="read",
        handler=lambda: "abcdef",
    ))
    settings = Settings(_env_file=None, model_tool_result_byte_budget=3)
    service = AgentService(settings, registry, tools=tools)

    await service.query(AgentQueryRequest(message="find evidence"))

    second_messages = provider.requests[1].messages
    tool_message = next(message for message in second_messages if message.role == "tool")
    assert tool_message.content == "abc"
    assert tool_message.tool_call_id == "call-1"
    assert tool_message.tool_name == "knowledge.search"
    assert provider.requests[0].messages[0].content == AGENT_WORKFLOW_PROMPT


@pytest.mark.asyncio
async def test_agent_does_not_call_provider_after_context_failure(tmp_path):
    provider = CountingProvider()
    registry = ProviderRegistry()
    registry.register(provider)
    settings = Settings(_env_file=None, model_context_byte_budget=1)
    journal = ActionJournal(tmp_path / "journal.sqlite3")
    service = AgentService(settings, registry, journal=journal)

    with pytest.raises(ContextBudgetError):
        await service.query(AgentQueryRequest(message="request"))

    assert provider.calls == 0
    with journal._connect() as connection:
        session_id = connection.execute(
            "SELECT session_id FROM agent_events LIMIT 1"
        ).fetchone()["session_id"]
    events = journal.list_events_for_session(session_id)
    assert [event.event_type for event in events] == ["user_request", "error"]
    assert events[-1].payload == {"stage": "context"}
