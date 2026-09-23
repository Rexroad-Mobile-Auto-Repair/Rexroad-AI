from __future__ import annotations

from pydantic import BaseModel, Field

from app.knowledge.multi_query import MultiQueryMatch, MultiQueryResult


class EvidenceRankedMatch(BaseModel):
    match: MultiQueryMatch
    rank_score: float
    ranked_position: int


class EvidenceRankingResult(BaseModel):
    ranked: list[EvidenceRankedMatch] = Field(default_factory=list)


class EvidenceRanker:
    _RRF_K = 60.0

    def rank(self, candidates: MultiQueryResult) -> EvidenceRankingResult:
        scored = []
        for match in candidates.candidates:
            raw_rank = match.raw_rank
            planned_rank = match.planned_rank
            score = (
                (1 / (self._RRF_K + raw_rank) if raw_rank is not None else 0)
                + (1 / (self._RRF_K + planned_rank) if planned_rank is not None else 0)
            )
            evidence = match.result.evidence
            scored.append((
                -score,
                raw_rank is None,
                raw_rank if raw_rank is not None else 0,
                planned_rank is None,
                planned_rank if planned_rank is not None else 0,
                evidence.file_path,
                evidence.line_start,
                match.result.chunk.chunk_id,
                match,
            ))
        scored.sort(key=lambda item: item[:-1])
        ranked = [
            EvidenceRankedMatch(match=item[-1], rank_score=-item[0], ranked_position=position)
            for position, item in enumerate(scored, start=1)
        ]
        return EvidenceRankingResult(ranked=ranked)
