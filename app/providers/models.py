from typing import Any, Literal

from pydantic import BaseModel, Field

from app.tools.models import ToolCall, ToolSpec


ProviderName = Literal[
    "openai",
    "anthropic",
    "ollama",
    "openai_compatible",
]


class ModelRequest(BaseModel):
    model: str
    messages: list[dict[str, str]]
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    tools: list[ToolSpec] = Field(default_factory=list)


class ModelResponse(BaseModel):
    provider: ProviderName
    model: str
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
