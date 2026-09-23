from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.knowledge.evidence_ranker import EvidenceRanker
from app.knowledge.evidence_selector import EvidenceSelector
from app.knowledge.multi_query import MultiQueryMatch, MultiQueryResult


class EvidenceRankingCase(BaseModel):
    case_id: str
    workspace: str
    query: str
    target_file_path: str
    target_symbol_name: str | None = None
    retrieval_limit: int = Field(default=10, ge=1, le=50)
    selection_limit: int = Field(default=8, ge=1, le=50)

    @field_validator("case_id", "workspace", "query", "target_file_path")
    @classmethod
    def _single_line_nonblank(cls, value: str) -> str:
        if not value.strip() or "\r" in value or "\n" in value:
            raise ValueError("value must be nonblank and single-line")
        return value

    @field_validator("target_symbol_name")
    @classmethod
    def _symbol_identity(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\r" in value or "\n" in value):
            raise ValueError("target_symbol_name must be nonblank and single-line")
        return value

    @field_validator("target_file_path")
    @classmethod
    def _safe_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or (len(normalized) >= 3 and normalized[1:3] ==":/") or any(part == ".." for part in normalized.split("/")):
            raise ValueError("target_file_path must be relative and safe")
        return normalized


class EvidenceRankingAcceptanceResult(BaseModel):
    case_id: str
    status: Literal["observed", "target_missing", "prerequisite_unavailable", "error"]
    safe_reason: str | None = None
    repeatable: bool | None = None
    target_found: bool
    raw_position: int | None = None
    planned_position: int | None = None
    raw_rank: int | None = None
    planned_rank: int | None = None
    candidate_position: int | None = None
    ranked_position: int | None = None
    selected_position: int | None = None
    freshness: str | None = None
    found_by: str | None = None
    rank_score: float | None = None


def _target(match: MultiQueryMatch, case: EvidenceRankingCase) -> bool:
    evidence = match.result.evidence if isinstance(match, MultiQueryMatch) else match.evidence
    return evidence.file_path == case.target_file_path and (case.target_symbol_name is None or evidence.symbol_name == case.target_symbol_name)


def _position(matches, case: EvidenceRankingCase) -> int | None:
    return next((index for index, match in enumerate(matches, 1) if _target(match, case)), None)


def _reordered(result: MultiQueryResult, ranked: list[MultiQueryMatch]) -> MultiQueryResult:
    return result.model_copy(update={"candidates": ranked})


class EvidenceRankingAcceptanceRunner:
    def __init__(self, retriever, ranker: EvidenceRanker | None = None, selector: EvidenceSelector | None = None) -> None:
        self._retriever = retriever
        self._ranker = ranker or EvidenceRanker()
        self._selector = selector or EvidenceSelector()

    async def run_case(self, case: EvidenceRankingCase) -> EvidenceRankingAcceptanceResult:
        try:
            first = await self._retriever.search(case.workspace, case.query, case.retrieval_limit, "hybrid")
            second = await self._retriever.search(case.workspace, case.query, case.retrieval_limit, "hybrid")
        except Exception:  # noqa: BLE001 - safe acceptance boundary
            return EvidenceRankingAcceptanceResult(case_id=case.case_id, status="error", safe_reason="retrieval_error", target_found=False)
        if first.model_dump(mode="json") != second.model_dump(mode="json"):
            return EvidenceRankingAcceptanceResult(case_id=case.case_id, status="error", safe_reason="repeatability_mismatch", repeatable=False, target_found=False)
        target = next((match for match in first.candidates if _target(match, case)), None)
        raw_position = _position(first.raw_results, case)
        planned_position = _position(first.planned_results, case)
        if target is None:
            return EvidenceRankingAcceptanceResult(case_id=case.case_id, status="target_missing", repeatable=True, target_found=False, raw_position=raw_position, planned_position=planned_position, safe_reason="target_not_in_candidate_pool")
        ranked = self._ranker.rank(first)
        ranked_target = next(entry for entry in ranked.ranked if entry.match is target)
        selected = self._selector.select(_reordered(first, [entry.match for entry in ranked.ranked]), case.selection_limit)
        selected_position = next((index for index, match in enumerate(selected.selected, 1) if match is target), None)
        freshness = target.result.evidence.freshness
        excluded_reason = None if selected_position else ("target_not_current" if freshness != "current" else "target_not_selected_limit")
        status = "prerequisite_unavailable" if freshness != "current" else "observed"
        return EvidenceRankingAcceptanceResult(case_id=case.case_id, status=status, repeatable=True, target_found=True, raw_position=raw_position, planned_position=planned_position, raw_rank=target.raw_rank, planned_rank=target.planned_rank, candidate_position=first.candidates.index(target) + 1, ranked_position=ranked_target.ranked_position, selected_position=selected_position, freshness=freshness, found_by=target.found_by, rank_score=ranked_target.rank_score, safe_reason=excluded_reason)
