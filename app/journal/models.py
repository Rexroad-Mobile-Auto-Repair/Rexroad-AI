from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ActionStatus = Literal[
    "success",
    "error",
]
EventType = Literal[
    "user_request", "model_response", "tool_call", "tool_result",
    "verification_request", "verification_response", "final_response", "error",
]


class ActionEntry(BaseModel):
    id: str
    session_id: str
    provider: str
    model: str
    tool: str
    permission: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: ActionStatus
    result_preview: str | None = None
    error: str | None = None
    created_at: datetime


class SessionSummary(BaseModel):
    session_id: str
    title: str = "New conversation"
    provider: str
    model: str
    action_count: int
    success_count: int
    error_count: int
    started_at: datetime
    last_action_at: datetime

class SessionDetail(BaseModel):
    summary: SessionSummary
    actions: list[ActionEntry]


class AgentEvent(BaseModel):
    id: str
    session_id: str
    sequence: int
    event_type: EventType
    action_id: str | None = None
    tool_call_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
