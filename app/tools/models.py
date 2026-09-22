from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


MessageRole = Literal[
    "system",
    "user",
    "assistant",
    "tool",
]


class ModelMessage(BaseModel):
    role: MessageRole
    content: str = ""
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
