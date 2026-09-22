from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ActionStatus = Literal[
    "success",
    "error",
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
    provider: str
    model: str
    action_count: int
    success_count: int
    error_count: int
    started_at: datetime
    last_action_at: datetime
