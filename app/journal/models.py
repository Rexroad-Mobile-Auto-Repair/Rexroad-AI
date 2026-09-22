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
