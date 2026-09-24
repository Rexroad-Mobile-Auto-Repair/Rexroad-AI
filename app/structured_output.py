from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.providers.models import ModelRequest, ModelResponse
from app.tools.models import ModelMessage

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(ValueError):
    """A bounded failure to produce the requested typed result."""


class StructuredOutputSpec(BaseModel):
    name: str
    json_schema: dict[str, Any]
    max_validation_attempts: int = 2


def _validation_feedback(error: ValidationError) -> str:
    items = []
    for detail in error.errors()[:8]:
        location = ".".join(str(part) for part in detail.get("loc", ())) or "root"
        message = str(detail.get("msg", "invalid value"))[:160]
        items.append(f"{location}: {message}")
    return "; ".join(items)[:1000]


class StructuredOutputService:
    MAX_RESPONSE_BYTES = 32_000
    MAX_ATTEMPTS = 2

    @staticmethod
    def validate_json(raw: str, result_type: type[T]) -> T:
        if len(raw.encode("utf-8")) > StructuredOutputService.MAX_RESPONSE_BYTES:
            raise StructuredOutputError("structured response exceeded the size limit")
        try:
            return result_type.model_validate(json.loads(raw))
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                reason = _validation_feedback(exc)
            else:
                reason = "valid JSON object required"
            raise StructuredOutputError(f"structured output validation failed: {reason}") from None

    async def generate(
        self,
        provider: Any,
        request: ModelRequest,
        result_type: type[T],
        *,
        name: str,
        max_validation_attempts: int = 2,
    ) -> T:
        attempts = max(1, min(self.MAX_ATTEMPTS, max_validation_attempts))
        schema = result_type.model_json_schema()
        spec = StructuredOutputSpec(name=name, json_schema=schema, max_validation_attempts=attempts)
        base_instruction = (
            f"Return only valid JSON for structured output '{spec.name}'. "
            f"Conform to this JSON Schema: {json.dumps(spec.json_schema, sort_keys=True)}"
        )
        messages = list(request.messages)
        if messages:
            messages[-1] = messages[-1].model_copy(
                update={"content": f"{messages[-1].content}\n\n{base_instruction}"}
            )
        last_feedback = "invalid structured output"
        for attempt in range(attempts):
            response: ModelResponse = await provider.generate(
                request.model_copy(update={"messages": messages, "structured_output": spec})
            )
            raw = response.content
            if len(raw.encode("utf-8")) > self.MAX_RESPONSE_BYTES:
                last_feedback = "structured response exceeded the size limit"
            else:
                try:
                    payload = json.loads(raw)
                    return result_type.model_validate(payload)
                except (json.JSONDecodeError, TypeError, ValidationError) as exc:
                    last_feedback = _validation_feedback(exc) if isinstance(exc, ValidationError) else "valid JSON object required"
            if attempt + 1 < attempts:
                messages.append(ModelMessage(role="assistant", content=raw[:2000]))
                messages.append(ModelMessage(role="user", content=f"Correct the JSON. Validation issue: {last_feedback}. Return JSON only."))
        raise StructuredOutputError(f"structured output validation failed: {last_feedback}")
