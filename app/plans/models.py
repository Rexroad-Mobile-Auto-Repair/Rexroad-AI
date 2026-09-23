from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

StepStatus = Literal["pending", "in_progress", "completed", "failed", "skipped"]
PlanStatus = Literal["active", "completed", "failed", "cancelled"]


class PlanStepCreate(BaseModel):
    title: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        if not value.strip() or "\r" in value or "\n" in value:
            raise ValueError("step title must be nonblank and single-line")
        return value


class PlanCreate(BaseModel):
    scope: str
    goal: str
    steps: list[PlanStepCreate]
    workspace: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("scope", "goal")
    @classmethod
    def valid_text(cls, value: str) -> str:
        if not value.strip() or "\r" in value or "\n" in value:
            raise ValueError("plan text must be nonblank and single-line")
        return value


class PlanStep(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    position: int
    title: str
    status: StepStatus = "pending"
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    reference: str | None = None


class ProjectPlan(BaseModel):
    id: str
    scope: str
    workspace: str | None = None
    goal: str
    status: PlanStatus = "active"
    steps: list[PlanStep]
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
