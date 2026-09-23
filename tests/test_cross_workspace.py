from pathlib import Path

import pytest

from app.knowledge.cross_workspace import CrossWorkspaceKnowledgeService
from app.knowledge.models import Evidence, KnowledgeChunk, KnowledgeSearchResult
from app.policy.workspaces import WorkspaceAccessError, WorkspaceRegistry


class FakeKnowledge:
    def __init__(self, results):
        self.results = results

    def search(self, workspace, query, limit, mode):
        return self.results.get(workspace, [])[:limit]


def result(workspace: str, path: str, score: float, rank: int) -> KnowledgeSearchResult:
    chunk = KnowledgeChunk(chunk_id=f"{workspace}-{path}", content="x", workspace=workspace, file_path=path, language="text", line_start=1, line_end=1, content_hash="h", indexed_at="2025-01-01T00:00:00Z")
    return KnowledgeSearchResult(chunk=chunk, evidence=Evidence(chunk_id=chunk.chunk_id, workspace=workspace, file_path=path, line_start=1, line_end=1, content_hash="h", score=score, rank=rank, freshness="current"))


def test_explicit_cross_workspace_merge_is_scoped_and_deterministic(tmp_path: Path) -> None:
    registry = WorkspaceRegistry({"a": tmp_path / "a", "b": tmp_path / "b", "c": tmp_path / "c"})
    service = CrossWorkspaceKnowledgeService(registry, FakeKnowledge({"a": [result("a", "same.py", 2, 1)], "b": [result("b", "same.py", 3, 1)], "c": [result("c", "secret.py", 9, 1)]}))
    merged = service.search_across_workspaces(["a", "b"], "same", limit=2)
    assert [item.chunk.workspace for item in merged] == ["b", "a"]
    assert [item.chunk.file_path for item in merged] == ["same.py", "same.py"]
    with pytest.raises(WorkspaceAccessError):
        service.search_across_workspaces(["a", "missing"], "x")
    with pytest.raises(ValueError):
        service.search_across_workspaces([], "x")
    assert all(item.chunk.workspace != "c" for item in merged)
