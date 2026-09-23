from pathlib import Path

import pytest

from app.knowledge.service import KnowledgeService
from app.knowledge.store import KnowledgeStore
from app.policy.workspaces import WorkspaceAccessError, WorkspaceRegistry


def service(tmp_path: Path) -> KnowledgeService:
    root = tmp_path / "workspace"
    root.mkdir(exist_ok=True)
    return KnowledgeService(WorkspaceRegistry({"docs": root}), KnowledgeStore(tmp_path / "index.sqlite3"))


def test_mixed_sources_have_types_and_are_searchable(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "README.md").write_text("# Guide\n\nDeployment instructions here.\n## Details\nMore details.\n", encoding="utf-8")
    (root / "notes.txt").write_text("First paragraph about billing.\n\nSecond paragraph about support.\n", encoding="utf-8")
    (root / "config.json").write_text('{"service": {"timeout": 30}}', encoding="utf-8")
    (root / "config.yaml").write_text("service:\n  timeout: 30\n", encoding="utf-8")
    (root / "code.py").write_text("def run():\n    return True\n", encoding="utf-8")
    knowledge = service(tmp_path)
    result = knowledge.index("docs")
    assert result.indexed_files == 5
    chunks = knowledge._store.list_workspace("docs")
    assert {chunk.source_type for chunk in chunks} == {"markdown", "text", "json", "yaml", "source_code"}
    assert knowledge.search("docs", "Deployment instructions")
    assert knowledge.search("docs", "timeout")


def test_markdown_sections_and_text_paragraphs_are_deterministic(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "doc.md").write_text("# One\nalpha\n## Two\nbeta\n", encoding="utf-8")
    (root / "notes.txt").write_text("one\n\ntwo\n", encoding="utf-8")
    knowledge = service(tmp_path)
    knowledge.index("docs")
    first = [item.model_dump(exclude={"indexed_at"}) for item in knowledge._store.list_workspace("docs")]
    knowledge.index("docs")
    second = [item.model_dump(exclude={"indexed_at"}) for item in knowledge._store.list_workspace("docs")]
    assert first == second
    markdown = [item for item in knowledge._store.list_workspace("docs") if item.source_type == "markdown"]
    assert [item.metadata["heading"] for item in markdown] == ["One", "Two"]
    assert [item.line_start for item in markdown] == [1, 3]


@pytest.mark.parametrize("name, content", [("bad.json", "{bad"), ("binary.txt", "a\x00b")])
def test_invalid_or_binary_documents_are_skipped(tmp_path, name, content):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / name).write_text(content, encoding="utf-8")
    knowledge = service(tmp_path)
    result = knowledge.index("docs")
    assert result.skipped_files == 1
    assert knowledge._store.list_workspace("docs") == []


def test_document_freshness_and_workspace_boundary(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    document = root / "notes.txt"
    document.write_text("current note\n", encoding="utf-8")
    knowledge = service(tmp_path)
    knowledge.index("docs")
    assert knowledge.search("docs", "current")[0].evidence.freshness == "current"
    document.write_text("changed note\n", encoding="utf-8")
    assert knowledge.search("docs", "current")[0].evidence.freshness == "stale"
    document.unlink()
    assert knowledge.search("docs", "current")[0].evidence.freshness == "missing"
    with pytest.raises(WorkspaceAccessError):
        knowledge.search("unknown", "note")
