from __future__ import annotations

import json

from app.context.models import ContextRequest, ContextResult, ContextTruncation
from app.tools.models import ModelMessage


class ContextBudgetError(ValueError):
    """The required provider context cannot fit within the configured budget."""


class ContextBuilder:
    @staticmethod
    def _message_bytes(message: ModelMessage) -> int:
        payload = message.model_dump(mode="json", exclude_none=True)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return len(encoded)

    @staticmethod
    def _prefix_by_bytes(content: str, budget: int) -> str:
        encoded = content.encode("utf-8")
        if len(encoded) <= budget:
            return content
        return encoded[:budget].decode("utf-8", errors="ignore")

    def build(self, request: ContextRequest) -> ContextResult:
        messages: list[ModelMessage] = []
        truncations: list[ContextTruncation] = []

        for index, original in enumerate(request.messages):
            message = original.model_copy(deep=True)
            if message.role == "tool":
                original_bytes = len(message.content.encode("utf-8"))
                bounded = self._prefix_by_bytes(
                    message.content,
                    request.tool_result_byte_budget,
                )
                bounded_bytes = len(bounded.encode("utf-8"))
                if bounded != message.content:
                    message.content = bounded
                    truncations.append(
                        ContextTruncation(
                            message_index=index,
                            original_byte_count=original_bytes,
                            bounded_byte_count=bounded_bytes,
                            reason="tool_result_byte_budget",
                        )
                    )
            messages.append(message)

        byte_count = sum(self._message_bytes(message) for message in messages)
        if byte_count > request.total_byte_budget:
            raise ContextBudgetError("Model context exceeds configured byte budget")

        return ContextResult(
            messages=messages,
            byte_count=byte_count,
            message_count=len(messages),
            truncations=truncations,
        )
