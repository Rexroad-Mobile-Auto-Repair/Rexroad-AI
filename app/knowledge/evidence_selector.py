from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.knowledge.multi_query import MultiQueryMatch, MultiQueryResult


class EvidenceSelectionDecision(BaseModel):
    chunk_id: str
    selected: bool
    reason: Literal["selected", "stale", "missing", "limit"]
    found_by: Literal["raw", "planned", "both"]
    raw_rank: int | None = None
    planned_rank: int | None = None
    freshness: Literal["current", "stale", "missing"]


class EvidenceSelectionResult(BaseModel):
    selected: list[MultiQueryMatch] = Field(default_factory=list)
    decisions: list[EvidenceSelectionDecision] = Field(default_factory=list)


class EvidenceSelector:
    def select(self, candidates: MultiQueryResult, limit: int) -> EvidenceSelectionResult:
        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50")

        selected: list[MultiQueryMatch] = []
        decisions: list[EvidenceSelectionDecision] = []
        for candidate in candidates.candidates:
            evidence = candidate.result.evidence
            freshness = evidence.freshness
            if freshness == "current" and len(selected) < limit:
                selected.append(candidate)
                reason: Literal["selected", "stale", "missing", "limit"] = "selected"
                is_selected = True
            elif freshness in {"stale", "missing"}:
                reason = freshness
                is_selected = False
            else:
                reason = "limit"
                is_selected = False
            decisions.append(EvidenceSelectionDecision(
                chunk_id=candidate.result.chunk.chunk_id,
                selected=is_selected,
                reason=reason,
                found_by=candidate.found_by,
                raw_rank=candidate.raw_rank,
                planned_rank=candidate.planned_rank,
                freshness=freshness,
            ))
        return EvidenceSelectionResult(selected=selected, decisions=decisions)
