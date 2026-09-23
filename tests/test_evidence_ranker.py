from datetime import UTC, datetime
from math import isclose

from app.knowledge.evidence_ranker import EvidenceRanker
from app.knowledge.models import Evidence, KnowledgeChunk, KnowledgeSearchResult
from app.knowledge.multi_query import MultiQueryMatch, MultiQueryResult


def item(identifier="x", freshness="current", found_by="raw", raw_rank=1, planned_rank=None, score=0.1, line=4, path=None):
    chunk = KnowledgeChunk(chunk_id=identifier, content=identifier, workspace="ws", file_path=path or f"src/{identifier}.py", language="python", line_start=line, line_end=line, symbol_name=identifier, content_hash=identifier, indexed_at=datetime(2026, 1, 1, tzinfo=UTC))
    result = KnowledgeSearchResult(chunk=chunk, evidence=Evidence(chunk_id=identifier, workspace="ws", file_path=chunk.file_path, line_start=line, line_end=line, symbol_name=identifier, content_hash=identifier, retrieval_method="hybrid", score=score, rank=raw_rank or planned_rank or 1, freshness=freshness))
    return MultiQueryMatch(result=result, found_by=found_by, raw_rank=raw_rank, planned_rank=planned_rank)


def pool(*items):
    return MultiQueryResult(original_query="question", planning_status="planned", raw_results=[], planned_results=[], candidates=list(items))


def test_empty_pool():
    assert EvidenceRanker().rank(pool()).ranked == []


def test_lane_scores_and_both_lane_sum():
    output = EvidenceRanker().rank(pool(
        item("raw", raw_rank=2),
        item("planned", found_by="planned", raw_rank=None, planned_rank=3),
        item("both", found_by="both", raw_rank=2, planned_rank=3),
    ))
    scores = {entry.match.result.chunk.chunk_id: entry.rank_score for entry in output.ranked}
    assert isclose(scores["raw"], 1 / 62)
    assert isclose(scores["planned"], 1 / 63)
    assert isclose(scores["both"], 1 / 62 + 1 / 63)
    assert output.ranked[0].match.result.chunk.chunk_id == "both"


def test_stable_sorting_and_exact_tie_breaks():
    first = item("a", raw_rank=4, planned_rank=4, found_by="both")
    second = item("b", raw_rank=4, planned_rank=4, found_by="both")
    output = EvidenceRanker().rank(pool(second, first))
    assert [entry.match.result.chunk.chunk_id for entry in output.ranked] == ["a", "b"]

    raw = item("raw", raw_rank=4)
    planned = item("planned", found_by="planned", raw_rank=None, planned_rank=4)
    output = EvidenceRanker().rank(pool(planned, raw))
    assert output.ranked[0].match.result.chunk.chunk_id == "raw"


def test_path_line_and_chunk_tie_breaks():
    one = item("z", raw_rank=4)
    two = item("a", raw_rank=4)
    two.result.evidence.file_path = "src/a.py"
    one.result.evidence.file_path = "src/b.py"
    output = EvidenceRanker().rank(pool(one, two))
    assert output.ranked[0].match.result.chunk.chunk_id == "a"


def test_evidence_score_is_ignored():
    output = EvidenceRanker().rank(pool(item("first", raw_rank=1, score=0.01), item("second", raw_rank=2, score=999.0)))
    assert [entry.match.result.chunk.chunk_id for entry in output.ranked] == ["first", "second"]


def test_line_number_breaks_equal_rank_ties():
    output = EvidenceRanker().rank(pool(item("late", raw_rank=4, line=20, path="same.py"), item("early", raw_rank=4, line=10, path="same.py")))
    assert [entry.match.result.chunk.chunk_id for entry in output.ranked] == ["early", "late"]


def test_ranker_returns_full_pool_over_eight_items():
    output = EvidenceRanker().rank(pool(*(item(str(index), raw_rank=index + 1) for index in range(12))))
    assert len(output.ranked) == 12
    assert [entry.ranked_position for entry in output.ranked] == list(range(1, 13))


def test_freshness_does_not_affect_ranking():
    output = EvidenceRanker().rank(pool(item("current", freshness="current", raw_rank=2), item("stale", freshness="stale", raw_rank=1), item("missing", freshness="missing", raw_rank=3)))
    assert [entry.match.result.chunk.chunk_id for entry in output.ranked] == ["stale", "current", "missing"]


def test_source_objects_and_input_are_unchanged():
    candidates = pool(item("a", raw_rank=2), item("b", raw_rank=1))
    before = candidates.model_dump(mode="json")
    source = candidates.candidates[0]
    result = source.result
    first = EvidenceRanker().rank(candidates)
    second = EvidenceRanker().rank(candidates)
    assert first.model_dump() == second.model_dump()
    assert first.ranked[1].match is source
    assert first.ranked[1].match.result is result
    assert candidates.model_dump(mode="json") == before


def test_identifier_bypass_pool_works_without_special_handling():
    candidates = pool(item("identifier", raw_rank=1))
    output = EvidenceRanker().rank(candidates)
    assert output.ranked[0].ranked_position == 1
    assert output.ranked[0].match.raw_rank == 1
    assert output.ranked[0].match.planned_rank is None
