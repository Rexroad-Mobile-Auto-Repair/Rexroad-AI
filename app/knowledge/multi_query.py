from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.knowledge.models import KnowledgeSearchResult
from app.knowledge.query_planner import (
    QueryPlanner,
    expanded_query,
    is_identifier_query,
)
from app.knowledge.service import KnowledgeService


class MultiQueryMatch(BaseModel):
    result: KnowledgeSearchResult
    found_by: Literal["raw", "planned", "both"]
    raw_rank: int | None = None
    planned_rank: int | None = None


class MultiQueryResult(BaseModel):
    original_query: str
    planned_query: str | None = None
    planning_status: str
    planning_safe_reason: str | None = None

    raw_results: list[KnowledgeSearchResult]
    planned_results: list[KnowledgeSearchResult]
    candidates: list[MultiQueryMatch]


class MultiQueryRetriever:
    def __init__(
        self,
        planner: QueryPlanner,
        knowledge: KnowledgeService,
    ) -> None:
        self._planner = planner
        self._knowledge = knowledge

    async def search(
        self,
        workspace: str,
        query: str,
        limit: int = 10,
        mode: str = "lexical",
    ) -> MultiQueryResult:
        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50")

        raw = self._knowledge.search(
            workspace,
            query,
            limit,
            mode,
        )

        if is_identifier_query(query):
            return self._result(
                query=query,
                planned_query=None,
                status="bypass",
                reason=None,
                raw=raw,
                planned=None,
            )

        plan = await self._planner.plan(query)
        planned_query = expanded_query(plan)

        if (
            plan.status == "fallback"
            or planned_query == query
        ):
            return self._result(
                query=query,
                planned_query=None,
                status=plan.status,
                reason=plan.safe_reason,
                raw=raw,
                planned=None,
            )

        planned = self._knowledge.search(
            workspace,
            planned_query,
            limit,
            mode,
        )

        return self._result(
            query=query,
            planned_query=planned_query,
            status=plan.status,
            reason=plan.safe_reason,
            raw=raw,
            planned=planned,
        )

    @staticmethod
    def _result(
        query: str,
        planned_query: str | None,
        status: str,
        reason: str | None,
        raw: list[KnowledgeSearchResult],
        planned: list[KnowledgeSearchResult] | None,
    ) -> MultiQueryResult:
        merged: dict[str, dict[str, object]] = {}

        for item in raw:
            merged[item.chunk.chunk_id] = {
                "result": item,
                "raw_rank": item.evidence.rank,
                "planned_rank": None,
            }

        planned_results = planned or []

        for item in planned_results:
            entry = merged.get(item.chunk.chunk_id)

            if entry is None:
                merged[item.chunk.chunk_id] = {
                    "result": item,
                    "raw_rank": None,
                    "planned_rank": item.evidence.rank,
                }
            else:
                entry["planned_rank"] = item.evidence.rank

        candidates: list[MultiQueryMatch] = []

        # Dict insertion order deliberately preserves:
        #
        # 1. raw result order
        # 2. planned-only additions in planned result order
        #
        # This is a deterministic candidate pool, NOT a merged relevance rank.
        for entry in merged.values():
            raw_rank = entry["raw_rank"]
            planned_rank = entry["planned_rank"]

            if raw_rank is not None and planned_rank is not None:
                found_by: Literal["raw", "planned", "both"] = "both"
            elif raw_rank is not None:
                found_by = "raw"
            else:
                found_by = "planned"

            candidates.append(
                MultiQueryMatch(
                    result=entry["result"],
                    found_by=found_by,
                    raw_rank=raw_rank,
                    planned_rank=planned_rank,
                )
            )

        return MultiQueryResult(
            original_query=query,
            planned_query=planned_query,
            planning_status=status,
            planning_safe_reason=reason,
            raw_results=raw,
            planned_results=planned_results,
            candidates=candidates,
        )
