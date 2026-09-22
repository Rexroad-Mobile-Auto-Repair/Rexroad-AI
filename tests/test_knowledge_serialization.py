import json
from datetime import UTC, datetime

import pytest

from app.knowledge.models import Evidence, KnowledgeChunk, KnowledgeSearchResult
from app.knowledge.serialization import serialize_search_results


def result(identifier: str, content: str = "content") -> KnowledgeSearchResult:
    chunk = KnowledgeChunk(
        chunk_id=identifier,
        content=content,
        workspace="repo",
        file_path="app/example.py",
        language="python",
        line_start=10,
        line_end=20,
        symbol_name="example",
        symbol_type="function",
        content_hash="hash",
        git_commit_sha="sha",
        indexed_at=datetime.now(UTC),
        metadata={"internal": "omit"},
    )
    evidence = Evidence(
        chunk_id=identifier,
        workspace="repo",
        file_path="app/example.py",
        line_start=10,
        line_end=20,
        symbol_name="example",
        symbol_type="function",
        content_hash="hash",
        git_commit_sha="sha",
        retrieval_method="lexical",
        score=10.0,
        rank=1,
        freshness="current",
    )
    return KnowledgeSearchResult(chunk=chunk, evidence=evidence)


def test_serializer_emits_stable_valid_json_and_preserves_fields():
    payload = serialize_search_results([result("one", "évidence")])
    decoded = json.loads(payload)
    assert decoded == [{
        "content": "évidence",
        "workspace": "repo",
        "relative_file_path": "app/example.py",
        "line_start": 10,
        "line_end": 20,
        "symbol_name": "example",
        "symbol_type": "function",
        "retrieval_method": "lexical",
        "rank": 1,
        "freshness": "current",
    }]
    assert "KnowledgeSearchResult" not in payload
    assert "internal" not in payload
    assert "hash" not in payload
    assert "sha" not in payload
    assert "score" not in payload
    assert "D:\\" not in payload


def test_serializer_preserves_order_and_does_not_mutate_results():
    items = [result("one"), result("two")]
    before = [item.model_dump() for item in items]
    decoded = json.loads(serialize_search_results(items))
    assert [item["content"] for item in decoded] == ["content", "content"]
    assert [item["rank"] for item in decoded] == [1, 1]
    assert [item.model_dump() for item in items] == before


def test_unsupported_knowledge_payload_fails_without_string_fallback():
    with pytest.raises(TypeError, match="unsupported result"):
        serialize_search_results([object()])
