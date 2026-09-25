"""Durable conversation compaction projection for Rexroad sessions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.context.tokens import estimate_messages_tokens
from app.journal.store import ActionJournal
from app.providers.models import ModelRequest
from app.tools.models import ModelMessage


@dataclass(frozen=True)
class CompactionResult:
    messages: list[ModelMessage]
    compacted: bool
    summary_source: str | None = None
    before_tokens: int = 0
    after_tokens: int = 0

class ContextCompactionService:
    SCHEMA_VERSION = "rexroad-compaction-v1"
    MAX_SUMMARY_CHARS = 12000

    def __init__(self, journal: ActionJournal | None = None) -> None:
        self._journal = journal

    @staticmethod
    def _serialize(messages: list[ModelMessage]) -> str:
        return "\n".join(f"{m.role}: {m.content[:4000]}" for m in messages if m.content)

    @classmethod
    def _fallback_summary(cls, messages: list[ModelMessage]) -> str:
        user = [m.content for m in messages if m.role == "user" and m.content]
        assistant = [m.content for m in messages if m.role == "assistant" and m.content]
        parts = [f"Conversation summary ({len(messages)} messages)."]
        if user:
            parts.append(f"Latest user goals/questions: {user[-3:]}")
        if assistant:
            parts.append(f"Latest assistant conclusions: {assistant[-3:]}")
        return "\n".join(parts)[: cls.MAX_SUMMARY_CHARS]

    async def _summarize(self, provider: Any, model: str, messages: list[ModelMessage], max_tokens: int) -> tuple[str, str]:
        prompt = ("Summarize this conversation for continuity. Preserve user goals, decisions, "
                  "unresolved questions, conclusions, and file/symbol references. Do not invent "
                  "tools, approvals, evidence, or security state. Those remain authoritative elsewhere.\n\n"
                  + self._serialize(messages))[: self.MAX_SUMMARY_CHARS]
        request = ModelRequest(model=model, messages=[ModelMessage(role="user", content=prompt)], tools=[])
        try:
            chunks: list[str] = []
            async for event in provider.stream(request):
                if event.type == "provider_error":
                    raise RuntimeError(event.message or "summary provider failed")
                if event.type == "text_delta":
                    chunks.append(event.text)
                elif event.type == "completed" and event.response is not None and event.response.content:
                    chunks = [event.response.content]
            summary = "".join(chunks).strip()[: max(400, max_tokens * 4)]
            if summary:
                return summary, "provider"
        except (RuntimeError, TypeError, ValueError):
            pass
        return self._fallback_summary(messages), "fallback"

    async def compact(self, *, session_id: str, messages: list[ModelMessage], provider: Any, model: str,
                      token_budget: int, trigger_ratio: float, recent_messages: int,
                      summary_token_budget: int, reason: str = "threshold") -> CompactionResult:
        before = estimate_messages_tokens(messages)
        if before <= int(token_budget * trigger_ratio) or len(messages) <= recent_messages:
            return CompactionResult(messages=messages, compacted=False, before_tokens=before, after_tokens=before)
        split = max(1, len(messages) - recent_messages)
        older, recent = messages[:split], messages[split:]
        summary, source = await self._summarize(provider, model, older, summary_token_budget)
        projected = [ModelMessage(role="assistant", content="[Rexroad compacted conversation summary]\n" + summary), *recent]
        after = estimate_messages_tokens(projected)
        if self._journal is not None:
            events = self._journal.list_events_for_session(session_id)
            relevant = [e for e in events if e.event_type in {"user_request", "final_response"}]
            start = relevant[0].sequence if relevant else None
            end = relevant[-1].sequence if relevant else None
            metadata = {"schema": self.SCHEMA_VERSION, "trigger": reason, "source_start": start,
                        "source_end": end, "before_tokens": before, "after_tokens": after,
                        "summary_source": source}
            self._journal.append_event(session_id=session_id, event_type="compaction_boundary", payload=metadata)
            self._journal.append_event(session_id=session_id, event_type="compaction_summary",
                                       payload={**metadata, "summary": summary,
                                                "preserved_messages": [m.model_dump(mode="json") for m in recent]})
        return CompactionResult(messages=projected, compacted=True, summary_source=source,
                                before_tokens=before, after_tokens=after)
