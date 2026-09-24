from typing import Any, Literal

from pydantic import BaseModel, Field

from app.tools.models import ModelMessage, ToolCall, ToolSpec

ProviderName = Literal[
    "openai",
    "anthropic",
    "ollama",
    "openai_compatible",
]


class ModelRequest(BaseModel):
    model: str
    messages: list[ModelMessage]
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    tools: list[ToolSpec] = Field(default_factory=list)
    structured_output: Any | None = None


class ModelResponse(BaseModel):
    provider: ProviderName
    model: str
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ProviderStreamEvent(BaseModel):
    type: Literal["text_delta", "completed", "provider_error"]
    text: str = ""
    response: ModelResponse | None = None
    message: str | None = None
