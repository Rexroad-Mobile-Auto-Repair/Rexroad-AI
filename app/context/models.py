import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.tools.models import ModelMessage


class ApprovedEvidence(BaseModel):
    evidence_id: str
    content: str
    workspace: str
    file_path: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    symbol_name: str | None = None
    freshness: Literal["current", "stale", "missing"]
    retrieval_method: Literal["lexical", "semantic", "hybrid"]
    rank: int = Field(ge=1)
    citation_alias: str | None = None

    @field_validator("workspace", "file_path", "symbol_name")
    @classmethod
    def _reject_provenance_injection(cls, value: str | None) -> str | None:
        if value is not None and ("\r" in value or "\n" in value):
            raise ValueError("provenance fields must not contain newlines")
        return value

    @field_validator("file_path")
    @classmethod
    def _require_relative_file_path(cls, value: str) -> str:
        if (
            value.startswith(("/", "\\"))
            or re.match(r"^[A-Za-z]:[\\/]", value) is not None
        ):
            raise ValueError("file_path must be workspace-relative")
        return value

    @model_validator(mode="after")
    def _validate_line_range(self) -> "ApprovedEvidence":
        if self.line_end < self.line_start:
            raise ValueError("line_end must be greater than or equal to line_start")
        return self

    @field_validator("citation_alias")
    @classmethod
    def _validate_citation_alias(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"E[1-9][0-9]*", value) is None:
            raise ValueError("citation_alias must use canonical E<n> syntax")
        return value


class ContextRequest(BaseModel):
    messages: list[ModelMessage]
    total_byte_budget: int = Field(gt=0)
    tool_result_byte_budget: int = Field(gt=0)
    approved_evidence: list[ApprovedEvidence] = Field(default_factory=list)
    max_evidence_items: int = Field(default=8, gt=0)
    total_evidence_byte_budget: int = Field(default=24576, gt=0)
    evidence_content_byte_budget: int = Field(default=8192, gt=0)
    supplemental_worker_context: list[object] = Field(default_factory=list, max_length=4)
    supplemental_context_byte_budget: int = Field(default=8192, gt=0)


class ContextTruncation(BaseModel):
    message_index: int
    original_byte_count: int
    bounded_byte_count: int
    reason: Literal["tool_result_byte_budget"]


class EvidenceDecision(BaseModel):
    evidence_id: str
    status: Literal["included", "excluded"]
    reason: Literal[
        "included",
        "stale",
        "missing",
        "max_evidence_items",
        "evidence_budget",
        "total_context_budget",
    ]
    freshness: Literal["current", "stale", "missing"]
    original_byte_count: int
    bounded_byte_count: int
    truncated: bool = False


class ContextResult(BaseModel):
    messages: list[ModelMessage]
    byte_count: int
    message_count: int
    truncations: list[ContextTruncation] = Field(default_factory=list)
    evidence_decisions: list[EvidenceDecision] = Field(default_factory=list)
