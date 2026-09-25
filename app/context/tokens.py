"""Provider-independent, bounded token estimation for conversation budgeting."""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from app.tools.models import ModelMessage

MAX_ESTIMATED_TOKENS = 10_000_000

def estimate_text_tokens(text: str) -> int:
    if not isinstance(text, str) or not text:
        return 0
    return min(MAX_ESTIMATED_TOKENS, max(1, (len(text.encode("utf-8")) + 3) // 4))

def estimate_message_tokens(message: ModelMessage) -> int:
    payload: dict[str, Any] = message.model_dump(mode="json", exclude_none=True)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return min(MAX_ESTIMATED_TOKENS, estimate_text_tokens(encoded) + 4)

def estimate_messages_tokens(messages: Iterable[ModelMessage]) -> int:
    total = 0
    for message in messages:
        total = min(MAX_ESTIMATED_TOKENS, total + estimate_message_tokens(message))
    return total
