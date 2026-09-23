from datetime import UTC, datetime

import pytest

from app.knowledge.models import (
    Evidence,
    KnowledgeChunk,
    KnowledgeSearchResult,
)
from app.knowledge.multi_query import MultiQueryRetriever
from app.knowledge.query_planner import RetrievalPlan


def result(
    chunk_id: str,
    rank: int,
    path: str = "a.py",
) -> KnowledgeSearchResult:
    chunk = KnowledgeChunk(
        chunk_id=chunk_id,
        content=chunk_id,
        workspace="ws",
        file_path=path,
        language="python",
        line_start=rank,
        line_end=rank,
        content_hash=chunk_id,
        indexed_at=datetime.now(UTC),
    )

    return KnowledgeSearchResult(
        chunk=chunk,
        evidence=Evidence(
            chunk_id=chunk_id,
            workspace="ws",
            file_path=path,
            line_start=rank,
            line_end=rank,
            content_hash=chunk_id,
            score=1.0,
            rank=rank,
            freshness="current",
        ),
    )


class Planner:
    def __init__(self, plan: RetrievalPlan):
        self.plan_value = plan
        self.calls = 0

    async def plan(self, query: str) -> RetrievalPlan:
        self.calls += 1
        return self.plan_value


class Knowledge:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def search(
        self,
        workspace,
        query,
        limit,
        mode,
    ):
        self.calls.append((workspace, query, limit, mode))
        return self.responses[len(self.calls) - 1]


@pytest.mark.asyncio
async def test_raw_and_planned_searches_are_preserved():
    raw = [
        result("a", 1),
        result("b", 2),
    ]

    planned = [
        result("b", 1),
        result("c", 2),
    ]

    planner = Planner(
        RetrievalPlan(
            original_query="where results",
            search_terms=["concept"],
        )
    )

    knowledge = Knowledge([raw, planned])

    output = await MultiQueryRetriever(
        planner,
        knowledge,
    ).search(
        "ws",
        "where results",
        10,
        "hybrid",
    )

    assert output.raw_results == raw
    assert output.planned_results == planned
    assert planner.calls == 1

    assert knowledge.calls == [
        (
            "ws",
            "where results",
            10,
            "hybrid",
        ),
        (
            "ws",
            "where results\ncode search concepts:\nconcept",
            10,
            "hybrid",
        ),
    ]


@pytest.mark.asyncio
async def test_candidate_pool_deduplicates_and_preserves_provenance():
    planner = Planner(
        RetrievalPlan(
            original_query="where results",
            search_terms=["concept"],
        )
    )

    knowledge = Knowledge(
        [
            [
                result("a", 1),
                result("b", 2),
            ],
            [
                result("b", 1),
                result("c", 2),
            ],
        ]
    )

    output = await MultiQueryRetriever(
        planner,
        knowledge,
    ).search(
        "ws",
        "where results",
        10,
        "hybrid",
    )

    assert [
        item.result.chunk.chunk_id
        for item in output.candidates
    ] == ["a", "b", "c"]

    assert [
        item.found_by
        for item in output.candidates
    ] == ["raw", "both", "planned"]

    by_id = {
        item.result.chunk.chunk_id: item
        for item in output.candidates
    }

    assert by_id["a"].raw_rank == 1
    assert by_id["a"].planned_rank is None

    assert by_id["b"].raw_rank == 2
    assert by_id["b"].planned_rank == 1

    assert by_id["c"].raw_rank is None
    assert by_id["c"].planned_rank == 2


@pytest.mark.asyncio
async def test_candidate_pool_is_not_truncated_after_merge():
    query = "where is this implemented"

    planner = Planner(
        RetrievalPlan(
            original_query=query,
            search_terms=["concept"],
        )
    )

    raw = [
        result(f"r{i}", i)
        for i in range(1, 11)
    ]

    planned = [
        result(f"p{i}", i)
        for i in range(1, 11)
    ]

    knowledge = Knowledge([raw, planned])

    output = await MultiQueryRetriever(
        planner,
        knowledge,
    ).search(
        "ws",
        query,
        10,
        "hybrid",
    )

    assert len(output.raw_results) == 10
    assert len(output.planned_results) == 10
    assert len(output.candidates) == 20

    assert [
        item.result.chunk.chunk_id
        for item in output.candidates[:10]
    ] == [
        f"r{i}"
        for i in range(1, 11)
    ]

    assert [
        item.result.chunk.chunk_id
        for item in output.candidates[10:]
    ] == [
        f"p{i}"
        for i in range(1, 11)
    ]


@pytest.mark.asyncio
async def test_identifier_bypasses_planner_and_uses_one_raw_search():
    planner = Planner(
        RetrievalPlan(
            original_query="symbol"
        )
    )

    raw = [result("x", 1)]
    knowledge = Knowledge([raw])

    output = await MultiQueryRetriever(
        planner,
        knowledge,
    ).search(
        "ws",
        "symbol",
        10,
        "hybrid",
    )

    assert planner.calls == 0
    assert len(knowledge.calls) == 1
    assert output.planning_status == "bypass"
    assert output.planned_query is None
    assert output.planned_results == []
    assert len(output.candidates) == 1
    assert output.candidates[0].found_by == "raw"


@pytest.mark.asyncio
async def test_fallback_is_raw_only():
    raw = [result("x", 1)]

    planner = Planner(
        RetrievalPlan(
            original_query="some question",
            status="fallback",
            safe_reason="provider_error",
        )
    )

    knowledge = Knowledge([raw])

    output = await MultiQueryRetriever(
        planner,
        knowledge,
    ).search(
        "ws",
        "some question",
        10,
        "semantic",
    )

    assert len(knowledge.calls) == 1
    assert output.planning_safe_reason == "provider_error"
    assert output.raw_results == raw
    assert output.planned_results == []
    assert output.candidates[0].result is raw[0]


@pytest.mark.asyncio
async def test_duplicate_planned_query_skips_second_search():
    planner = Planner(
        RetrievalPlan(
            original_query="some question",
            search_terms=[],
            identifiers=[],
        )
    )

    raw = [result("x", 1)]
    knowledge = Knowledge([raw])

    output = await MultiQueryRetriever(
        planner,
        knowledge,
    ).search(
        "ws",
        "some question",
        10,
        "lexical",
    )

    assert planner.calls == 1
    assert len(knowledge.calls) == 1
    assert output.planned_query is None
    assert output.planned_results == []


@pytest.mark.asyncio
async def test_search_limit_applies_per_query_lane():
    query = "where is this implemented"

    planner = Planner(
        RetrievalPlan(
            original_query=query,
            search_terms=["concept"],
        )
    )

    knowledge = Knowledge(
        [
            [result("a", 1)],
            [result("b", 1)],
        ]
    )

    await MultiQueryRetriever(
        planner,
        knowledge,
    ).search(
        "ws",
        query,
        3,
        "semantic",
    )

    assert knowledge.calls[0][2] == 3
    assert knowledge.calls[1][2] == 3


@pytest.mark.asyncio
async def test_result_models_do_not_mutate_knowledge_objects():
    query = "where is this implemented"

    raw = result("x", 1)
    planned = result("y", 1)

    raw_before = raw.model_dump(mode="json")
    planned_before = planned.model_dump(mode="json")

    planner = Planner(
        RetrievalPlan(
            original_query=query,
            search_terms=["concept"],
        )
    )

    knowledge = Knowledge(
        [
            [raw],
            [planned],
        ]
    )

    await MultiQueryRetriever(
        planner,
        knowledge,
    ).search(
        "ws",
        query,
        10,
        "hybrid",
    )

    assert raw.model_dump(mode="json") == raw_before
    assert planned.model_dump(mode="json") == planned_before


@pytest.mark.asyncio
async def test_original_query_and_planned_query_are_preserved():
    query = "  where is this implemented?  "

    planner = Planner(
        RetrievalPlan(
            original_query=query,
            search_terms=["implementation"],
        )
    )

    knowledge = Knowledge(
        [
            [result("a", 1)],
            [result("b", 1)],
        ]
    )

    output = await MultiQueryRetriever(
        planner,
        knowledge,
    ).search(
        "ws",
        query,
        10,
        "hybrid",
    )

    assert output.original_query == query
    assert output.planned_query == (
        query
        + "\ncode search concepts:\n"
        + "implementation"
    )
