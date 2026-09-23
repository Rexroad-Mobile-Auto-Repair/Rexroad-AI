from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MemoryCategory = Literal["fact", "decision", "preference", "task", "outcome", "note"]
MemoryStatus = Literal["active", "resolved", "superseded"]
MemoryProvenance = Literal["user_explicit", "agent_recorded", "research_outcome", "project_process", "manual_system"]


class MemoryRecord(BaseModel):
    id: str
    scope: str
    category: MemoryCategory
    content: str
    status: MemoryStatus
    provenance: MemoryProvenance
    source_reference: str | None = None
    supersedes: str | None = None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("scope", "content")
    @classmethod
    def _safe_text(cls, value: str) -> str:
        if not value.strip() or "\r" in value or "\n" in value:
            raise ValueError("memory text must be nonblank and single-line")
        return value


class MemoryCreate(BaseModel):
    scope: str
    category: MemoryCategory
    content: str
    provenance: MemoryProvenance = "user_explicit"
    source_reference: str | None = None
    supersedes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryUpdate(BaseModel):
    content: str | None = None
    status: MemoryStatus | None = None
    metadata: dict[str, Any] | None = None

