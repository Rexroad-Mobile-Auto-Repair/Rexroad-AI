from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.knowledge.models import Evidence
from app.providers.models import ProviderName

ResearchStatus = Literal[
    "success",
    "no_evidence",
    "retrieval_error",
    "context_error",
    "provider_error",
]
CitationStatus = Literal["none", "verified", "invalid"]


class ResearchRequest(BaseModel):
    query: str
    workspace: str
    mode: Literal["lexical", "semantic", "hybrid"] = "lexical"
    limit: int = Field(default=10, ge=1, le=50)
    provider: ProviderName | None = None
    model: str | None = None
    contribution_ids: list[str] = Field(default_factory=list, max_length=4)
    incorporation_ids: list[str] = Field(default_factory=list, max_length=4)
    verified_workflow_ids: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("query")
    @classmethod
    def _query_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be empty")
        return value


class ResearchEvidenceReference(BaseModel):
    evidence_id: str
    workspace: str
    relative_file_path: str
    line_start: int
    line_end: int
    symbol_name: str | None = None
    symbol_type: str | None = None
    freshness: Literal["current", "stale", "missing"]
    retrieval_method: Literal["lexical", "semantic", "hybrid"]
    rank: int

    @classmethod
    def from_evidence(cls, evidence: Evidence, evidence_id: str) -> "ResearchEvidenceReference":
        return cls(
            evidence_id=evidence_id,
            workspace=evidence.workspace,
            relative_file_path=evidence.file_path,
            line_start=evidence.line_start,
            line_end=evidence.line_end,
            symbol_name=evidence.symbol_name,
            symbol_type=evidence.symbol_type,
            freshness=evidence.freshness,
            retrieval_method=evidence.retrieval_method,
            rank=evidence.rank,
        )


class ResearchCitation(BaseModel):
    alias: str
    evidence: ResearchEvidenceReference


class ResearchAnswer(BaseModel):
    status: ResearchStatus
    answer: str | None = None
    provider: str | None = None
    model: str | None = None
    research_id: str
    evidence: list[ResearchEvidenceReference] = Field(default_factory=list)
    citations: list[ResearchCitation] = Field(default_factory=list)
    citation_status: CitationStatus = "none"
    invalid_citation_aliases: list[str] = Field(default_factory=list)
