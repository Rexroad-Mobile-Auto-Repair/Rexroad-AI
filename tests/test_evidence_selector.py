from datetime import UTC, datetime

import pytest

from app.knowledge.evidence_selector import EvidenceSelector
from app.knowledge.models import Evidence, KnowledgeChunk, KnowledgeSearchResult
from app.knowledge.multi_query import MultiQueryMatch, MultiQueryResult


def item(identifier: str, freshness: str = "current", found_by: str = "raw", raw_rank: int | None = 1, planned_rank: int | None = None) -> MultiQueryMatch:
    chunk = KnowledgeChunk(
        chunk_id=identifier, content=f"source-{identifier}", workspace="ws",
        file_path=f"src/{identifier}.py", language="python", line_start=4,
        line_end=9, symbol_name=f"symbol_{identifier}", symbol_type="function",
        content_hash=f"hash-{identifier}", git_commit_sha="sha",
        indexed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    result = KnowledgeSearchResult(
        chunk=chunk,
        evidence=Evidence(
            chunk_id=identifier, workspace="ws", file_path=chunk.file_path,
            line_start=4, line_end=9, symbol_name=chunk.symbol_name,
            symbol_type=chunk.symbol_type, content_hash=chunk.content_hash,
            git_commit_sha="sha", retrieval_method="hybrid", score=0.8,
            rank=raw_rank or planned_rank or 1, freshness=freshness,
        ),
    )
    return MultiQueryMatch(result=result, found_by=found_by, raw_rank=raw_rank, planned_rank=planned_rank)


def pool(*items: MultiQueryMatch) -> MultiQueryResult:
    return MultiQueryResult(
        original_query="question", planning_status="planned", raw_results=[],
        planned_results=[], candidates=list(items),
    )


def test_empty_pool():
    output = EvidenceSelector().select(pool(), 10)
    assert output.selected == [] and output.decisions == []


def test_current_selected_and_order_preserved():
    candidates = pool(item("a"), item("b"))
    output = EvidenceSelector().select(candidates, 10)
    assert [entry.result.chunk.chunk_id for entry in output.selected] == ["a", "b"]
    assert [entry.chunk_id for entry in output.decisions] == ["a", "b"]
    assert all(decision.reason == "selected" for decision in output.decisions)


def test_stale_and_missing_are_excluded_with_reasons():
    output = EvidenceSelector().select(pool(item("s", "stale"), item("m", "missing")), 10)
    assert output.selected == []
    assert [decision.reason for decision in output.decisions] == ["stale", "missing"]


def test_limit_excludes_remaining_current_candidates():
    output = EvidenceSelector().select(pool(item("a"), item("b"), item("c")), 2)
    assert [entry.result.chunk.chunk_id for entry in output.selected] == ["a", "b"]
    assert output.decisions[-1].reason == "limit"
    assert not output.decisions[-1].selected


def test_all_lane_provenance_and_source_object_are_preserved():
    raw = item("raw", found_by="raw", raw_rank=3)
    planned = item("planned", found_by="planned", raw_rank=None, planned_rank=7)
    both = item("both", found_by="both", raw_rank=2, planned_rank=1)
    candidates = pool(raw, planned, both)
    before = candidates.model_dump(mode="json")
    output = EvidenceSelector().select(candidates, 10)
    assert output.selected[0].result is raw.result
    assert output.selected[1].result is planned.result
    assert output.selected[2].result is both.result
    assert [(d.found_by, d.raw_rank, d.planned_rank) for d in output.decisions] == [
        ("raw", 3, None), ("planned", None, 7), ("both", 2, 1)
    ]
    decision = output.decisions[0]
    assert (decision.freshness, decision.chunk_id) == ("current", "raw")
    assert output.selected[0].result.evidence.file_path == "src/raw.py"
    assert output.selected[0].result.evidence.line_start == 4
    assert output.selected[0].result.evidence.symbol_name == "symbol_raw"
    assert candidates.model_dump(mode="json") == before


def test_repeated_selection_is_deterministic():
    candidates = pool(item("a"), item("s", "stale"), item("b"))
    first = EvidenceSelector().select(candidates, 1)
    second = EvidenceSelector().select(candidates, 1)
    assert first.model_dump() == second.model_dump()


@pytest.mark.parametrize("limit", [0, 51])
def test_limit_is_validated(limit):
    with pytest.raises(ValueError, match="between 1 and 50"):
        EvidenceSelector().select(pool(item("a")), limit)


def test_identifier_bypass_candidates_need_no_special_handling():
    candidates = MultiQueryResult(
        original_query="build_report", planning_status="bypass", raw_results=[],
        planned_results=[], candidates=[item("identifier")],
    )
    output = EvidenceSelector().select(candidates, 1)
    assert [entry.result.chunk.chunk_id for entry in output.selected] == ["identifier"]
