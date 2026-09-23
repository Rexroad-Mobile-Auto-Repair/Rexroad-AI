from pathlib import Path

import pytest

from app.knowledge.extractors import PlainTextExtractor
from app.knowledge.service import KnowledgeService
from app.knowledge.store import KnowledgeStore
from app.policy.workspaces import WorkspaceAccessError, WorkspaceRegistry


def make_service(tmp_path: Path, **limits) -> KnowledgeService:
    root = tmp_path / "workspace"
    root.mkdir(exist_ok=True)
    return KnowledgeService(WorkspaceRegistry({"docs": root}), KnowledgeStore(tmp_path / "index.sqlite3"), **limits)


def test_extracted_text_sections_are_searchable_and_provenant(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "manual.extract.txt").write_text("first page phrase\fsecond page phrase", encoding="utf-8")
    service = make_service(tmp_path)
    assert service.index("docs").indexed_chunks == 2
    result = service.search("docs", "second page phrase")[0]
    assert result.chunk.source_type == "extracted_document"
    assert result.chunk.metadata["section"] == "2"
    assert "source_hash" in result.chunk.metadata
    assert result.evidence.workspace == "docs"
    assert result.evidence.file_path == "manual.extract.txt"
    assert result.evidence.freshness == "current"


def test_extractor_output_and_chunk_ids_are_deterministic(tmp_path):
    path = tmp_path / "manual.extract.txt"
    path.write_text("one\ftwo", encoding="utf-8")
    extractor = PlainTextExtractor()
    assert extractor.supports(path)
    first = extractor.extract(path)
    second = extractor.extract(path)
    assert first == second
    service = make_service(tmp_path)
    service.index("docs")
    ids = [chunk.chunk_id for chunk in service._store.list_workspace("docs")]
    service.index("docs")
    assert ids == [chunk.chunk_id for chunk in service._store.list_workspace("docs")]


def test_limits_and_failures_skip_extracted_sources(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "large.extract.txt").write_text("12345", encoding="utf-8")
    service = make_service(tmp_path, max_source_bytes=4)
    assert service.index("docs").skipped_files == 1
    (root / "large.extract.txt").write_text("one\ftwo", encoding="utf-8")
    service = make_service(tmp_path, max_extracted_sections=1)
    assert service.index("docs").skipped_files == 1


def test_unknown_workspace_is_rejected(tmp_path):
    service = make_service(tmp_path)
    with pytest.raises(WorkspaceAccessError):
        service.index("unknown")
