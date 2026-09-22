from typing import Literal

from pydantic import BaseModel, Field

from app.tools.models import ModelMessage


class ContextRequest(BaseModel):
    messages: list[ModelMessage]
    total_byte_budget: int = Field(gt=0)
    tool_result_byte_budget: int = Field(gt=0)


class ContextTruncation(BaseModel):
    message_index: int
    original_byte_count: int
    bounded_byte_count: int
    reason: Literal["tool_result_byte_budget"]


class ContextResult(BaseModel):
    messages: list[ModelMessage]
    byte_count: int
    message_count: int
    truncations: list[ContextTruncation] = Field(default_factory=list)
