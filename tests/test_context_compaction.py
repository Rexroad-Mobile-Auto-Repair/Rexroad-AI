from __future__ import annotations

import asyncio

from app.context.compaction import ContextCompactionService
from app.context.tokens import estimate_messages_tokens, estimate_text_tokens
from app.journal.store import ActionJournal
from app.providers.models import ModelResponse, ProviderStreamEvent
from app.tools.models import ModelMessage


class FakeProvider:
    def __init__(self, summary="provider summary", fail=False):
        self.summary = summary
        self.fail = fail

    async def stream(self, request):
        if self.fail:
            yield ProviderStreamEvent(type="provider_error", message="failed")
            return
        yield ProviderStreamEvent(
            type="completed",
            response=ModelResponse(provider="openai_compatible", model=request.model, content=self.summary),
        )


def test_token_estimation_is_deterministic_and_bounded():
    assert estimate_text_tokens("hello") == estimate_text_tokens("hello")
    assert estimate_text_tokens("x" * 100) > estimate_text_tokens("x")
    assert estimate_messages_tokens([ModelMessage(role="user", content="hello")]) > 0


def test_compaction_threshold_and_durable_metadata(tmp_path):
    journal = ActionJournal(tmp_path / "journal.sqlite3")
    messages = [ModelMessage(role="user", content="x" * 300) for _ in range(4)]
    result = asyncio.run(ContextCompactionService(journal).compact(
        session_id="s", messages=messages, provider=FakeProvider(), model="test",
        token_budget=100, trigger_ratio=.5, recent_messages=2, summary_token_budget=100,
    ))
    assert result.compacted is True
    events = journal.list_events_for_session("s")
    assert [event.event_type for event in events] == ["compaction_boundary", "compaction_summary"]
    assert events[-1].payload["summary_source"] == "provider"
    assert len(events[-1].payload["preserved_messages"]) == 2


def test_no_compaction_below_threshold(tmp_path):
    result = asyncio.run(ContextCompactionService(ActionJournal(tmp_path / "j.sqlite3")).compact(
        session_id="s", messages=[ModelMessage(role="user", content="small")], provider=FakeProvider(),
        model="test", token_budget=1000, trigger_ratio=.85, recent_messages=2, summary_token_budget=100,
    ))
    assert result.compacted is False


def test_provider_failure_uses_deterministic_fallback(tmp_path):
    messages = [ModelMessage(role="user", content="important goal") for _ in range(4)]
    result = asyncio.run(ContextCompactionService(ActionJournal(tmp_path / "j.sqlite3")).compact(
        session_id="s", messages=messages, provider=FakeProvider(fail=True), model="test",
        token_budget=10, trigger_ratio=.5, recent_messages=2, summary_token_budget=100,
    ))
    assert result.summary_source == "fallback"
    assert "important goal" in result.messages[0].content
