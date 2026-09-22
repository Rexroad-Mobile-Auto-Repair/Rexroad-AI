import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.knowledge.embeddings import OpenAICompatibleEmbeddingProvider
from app.knowledge.models import KnowledgeChunk
from app.knowledge.service import KnowledgeService, embedding_text
from app.knowledge.store import KnowledgeStore
from app.policy.workspaces import WorkspaceAccessError, WorkspaceRegistry


def build_service(tmp_path: Path, root: Path) -> KnowledgeService:
    return KnowledgeService(
        WorkspaceRegistry({"repo": root}),
        KnowledgeStore(tmp_path / "knowledge.sqlite3"),
    )


class FakeEmbeddingProvider:
    identity = "fake:semantic-v1"
    dimensions = 3

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls = getattr(self, "calls", 0) + 1
        vectors = []
        for text in texts:
            value = text.casefold()
            vectors.append([
                1.0 if any(token in value for token in ("store", "persist", "database")) else 0.0,
                1.0 if any(token in value for token in ("indexability", "google", "search")) else 0.0,
                1.0 if any(token in value for token in ("identifier", "crawl_id")) else 0.0,
            ])
        return vectors


def build_semantic_service(tmp_path: Path, root: Path) -> KnowledgeService:
    return KnowledgeService(
        WorkspaceRegistry({"repo": root}),
        KnowledgeStore(tmp_path / "knowledge.sqlite3"),
        FakeEmbeddingProvider(),
    )


def write_source(root: Path) -> Path:
    source = root / "sample.py"
    source.write_text(
        "VALUE = 1\n\n"
        "def build_internal_links_report(crawl_id: str) -> str:\n"
        "    return crawl_id\n\n"
        "class CrawlHistory:\n"
        "    def page_link_metrics(self) -> str:\n"
        "        return 'metrics'\n",
        encoding="utf-8",
    )
    return source


def test_python_chunks_preserve_symbols_lines_and_ids(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_source(root)
    service = build_service(tmp_path, root)

    first = service.index("repo")
    chunks = service._store.list_workspace("repo")
    second = service.index("repo")
    repeated_chunks = service._store.list_workspace("repo")

    assert first.indexed_files == 1
    assert first.indexed_chunks == 4
    assert second.indexed_chunks == 4
    assert [chunk.chunk_id for chunk in chunks] == [chunk.chunk_id for chunk in repeated_chunks]
    function = next(chunk for chunk in chunks if chunk.symbol_name == "build_internal_links_report")
    method = next(chunk for chunk in chunks if chunk.symbol_name == "page_link_metrics")
    assert (function.line_start, function.line_end) == (3, 4)
    assert method.parent_symbol == "CrawlHistory"
    assert method.symbol_type == "method"


def test_oversized_units_are_split_at_deterministic_line_boundaries(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    lines = ["def oversized():\n"] + ["    value = 1\n"] * 450
    (root / "large.py").write_text("".join(lines), encoding="utf-8")
    service = build_service(tmp_path, root)

    service.index("repo")
    chunks = service._store.list_workspace("repo")

    assert [(chunk.line_start, chunk.line_end) for chunk in chunks] == [
        (1, 200), (201, 400), (401, 451)
    ]
    assert all(chunk.symbol_name == "oversized" for chunk in chunks)


def test_ignored_and_unreadable_source_is_not_indexed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_source(root)
    ignored = root / ".venv"
    ignored.mkdir()
    (ignored / "ignored.py").write_text("def ignored(): pass\n", encoding="utf-8")
    (root / "binary.py").write_bytes(b"\x00not source")
    service = build_service(tmp_path, root)

    result = service.index("repo")

    assert result.indexed_files == 1
    assert result.skipped_files == 1
    assert all(chunk.file_path == "sample.py" for chunk in service._store.list_workspace("repo"))


def test_workspace_root_protection_is_preserved(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    service = build_service(tmp_path, root)

    with pytest.raises(WorkspaceAccessError):
        service._workspaces.resolve_path("repo", "../outside.py")


def test_symlink_to_source_outside_workspace_is_not_indexed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside.py"
    root.mkdir()
    outside.write_text("def outside_only_identifier():\n    pass\n", encoding="utf-8")
    link = root / "linked.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    service = build_service(tmp_path, root)

    result = service.index("repo")

    assert result.indexed_files == 0
    assert service.search("repo", "outside_only_identifier") == []
    assert service._store.list_workspace("repo") == []


def test_search_rejects_workspace_removed_from_registry(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_source(root)
    workspaces = WorkspaceRegistry({"repo": root})
    service = KnowledgeService(workspaces, KnowledgeStore(tmp_path / "knowledge.sqlite3"))
    service.index("repo")
    del workspaces._workspaces["repo"]

    with pytest.raises(WorkspaceAccessError, match="Unknown workspace"):
        service.search("repo", "build_internal_links_report")


def test_lexical_search_returns_ranked_evidence_and_content_hash(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_source(root)
    service = build_service(tmp_path, root)
    service.index("repo")

    exact = service.search("repo", "build_internal_links_report")
    natural = service.search("repo", "crawl history page metrics")

    assert exact[0].chunk.symbol_name == "build_internal_links_report"
    assert exact[0].evidence.retrieval_method == "lexical"
    assert exact[0].evidence.rank == 1
    assert exact[0].evidence.content_hash == exact[0].chunk.content_hash
    assert any(item.chunk.symbol_name == "page_link_metrics" for item in natural)
    assert [item.evidence.rank for item in natural] == list(range(1, len(natural) + 1))
    assert [item.chunk.chunk_id for item in natural] == [
        item.chunk.chunk_id
        for item in service.search("repo", "crawl history page metrics")
    ]


def test_freshness_handles_current_stale_missing_and_non_git(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = write_source(root)
    service = build_service(tmp_path, root)
    result = service.index("repo")
    current = service.search("repo", "build_internal_links_report")[0]
    source.write_text("def changed():\n    pass\n", encoding="utf-8")
    stale = service.search("repo", "build_internal_links_report")[0]
    source.unlink()
    missing = service.search("repo", "build_internal_links_report")[0]

    assert result.git_commit_sha is None
    assert current.evidence.freshness == "current"
    assert stale.evidence.freshness == "stale"
    assert missing.evidence.freshness == "missing"


def test_git_sha_is_recorded_and_commit_change_marks_chunks_stale(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_source(root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "sample.py"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "initial"],
        cwd=root,
        check=True,
    )
    service = build_service(tmp_path, root)
    result = service.index("repo")
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "next"],
        cwd=root,
        check=True,
    )

    found = service.search("repo", "build_internal_links_report")

    assert result.git_commit_sha is not None
    assert found[0].evidence.git_commit_sha == result.git_commit_sha
    assert found[0].evidence.freshness == "stale"


def test_empty_index_and_invalid_limit_are_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    service = build_service(tmp_path, root)

    assert service.search("repo", "anything") == []
    with pytest.raises(ValueError, match="between 1 and 50"):
        service.search("repo", "anything", limit=0)


def test_semantic_retrieval_can_match_conceptual_language(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "storage.py").write_text("def record_analysis_run():\n    persist_to_database()\n", encoding="utf-8")
    service = build_semantic_service(tmp_path, root)
    service.index("repo")

    results = service.search("repo", "where are results persisted", mode="semantic")

    assert results[0].chunk.symbol_name == "record_analysis_run"
    assert results[0].evidence.retrieval_method == "semantic"


def test_hybrid_fuses_duplicates_and_exact_identifier_stays_first(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_source(root)
    service = build_semantic_service(tmp_path, root)
    service.index("repo")

    results = service.search("repo", "build_internal_links_report", mode="hybrid")
    chunk_ids = [result.chunk.chunk_id for result in results]

    assert results[0].chunk.symbol_name == "build_internal_links_report"
    assert results[0].evidence.retrieval_method == "hybrid"
    assert len(chunk_ids) == len(set(chunk_ids))


def test_semantic_ties_are_deterministic_and_lexical_default_is_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "b.py").write_text("def beta():\n    return 'database'\n", encoding="utf-8")
    (root / "a.py").write_text("def alpha():\n    return 'database'\n", encoding="utf-8")
    service = build_semantic_service(tmp_path, root)
    service.index("repo")

    semantic = service.search("repo", "persisted results", mode="semantic")
    lexical = service.search("repo", "database", mode="lexical")

    assert [item.chunk.file_path for item in semantic] == ["a.py", "b.py"]
    assert all(item.evidence.retrieval_method == "lexical" for item in lexical)


def test_embedding_cache_invalidates_content_provider_and_dimensions(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_source(root)
    provider = FakeEmbeddingProvider()
    service = KnowledgeService(WorkspaceRegistry({"repo": root}), KnowledgeStore(tmp_path / "knowledge.sqlite3"), provider)
    service.index("repo")
    target = next(chunk for chunk in service._store.list_workspace("repo") if chunk.symbol_name == "build_internal_links_report")
    first = service._store.list_embeddings([target.chunk_id])
    source = root / "sample.py"
    source.write_text(source.read_text(encoding="utf-8").replace("return crawl_id", "return 'changed'"), encoding="utf-8")
    service.index("repo")
    target = next(chunk for chunk in service._store.list_workspace("repo") if chunk.symbol_name == "build_internal_links_report")
    changed = service._store.list_embeddings([target.chunk_id])
    assert first != changed

    provider.identity = "fake:semantic-v2"
    service.index("repo")
    target = next(chunk for chunk in service._store.list_workspace("repo") if chunk.symbol_name == "build_internal_links_report")
    replaced = service._store.list_embeddings([target.chunk_id])
    assert replaced[next(iter(replaced))]["provider_identity"].startswith("fake:semantic-v2|")


def test_embedding_provider_identity_normalizes_endpoint_without_credentials() -> None:
    first = OpenAICompatibleEmbeddingProvider("HTTP://LOCALHOST:80/v1/", "same-model", "secret-a")
    equivalent = OpenAICompatibleEmbeddingProvider("http://localhost/v1", "same-model", "secret-b")
    different = OpenAICompatibleEmbeddingProvider("http://127.0.0.1/v1", "same-model")

    assert first.identity == equivalent.identity
    assert first.identity != different.identity
    assert "secret" not in first.identity


def test_embedding_text_contains_structural_metadata_and_content() -> None:
    value = KnowledgeChunk(
        chunk_id="id", content="return value", workspace="repo", file_path="app/example.py",
        language="python", line_start=1, line_end=1, symbol_name="example", symbol_type="function",
        parent_symbol="Parent", content_hash="hash", indexed_at=datetime.now(UTC),
    )
    text = embedding_text(value)

    assert "file: app/example.py" in text
    assert "language: python" in text
    assert "symbol: example" in text
    assert "symbol_type: function" in text
    assert "parent_symbol: Parent" in text
    assert "return value" in text
    assert text == embedding_text(value)


def test_old_raw_content_cache_identity_is_not_reused(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_source(root)
    provider = FakeEmbeddingProvider()
    store = KnowledgeStore(tmp_path / "knowledge.sqlite3")
    service = KnowledgeService(WorkspaceRegistry({"repo": root}), store, provider)
    service_without_provider = build_service(tmp_path, root)
    service_without_provider.index("repo")
    chunk = next(item for item in store.list_workspace("repo") if item.symbol_name == "build_internal_links_report")
    store.upsert_embeddings([{
        "chunk_id": chunk.chunk_id, "content_hash": chunk.content_hash,
        "provider_identity": provider.identity, "dimensions": 3, "vector": [1.0, 0.0, 0.0],
    }])

    service.index("repo")

    assert provider.calls == 1
    record = store.list_embeddings([chunk.chunk_id])[chunk.chunk_id]
    assert "representation:knowledge-chunk-structure-v1" in record["provider_identity"]


def test_semantic_dimension_mismatch_is_ignored_safely(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_source(root)
    service = build_semantic_service(tmp_path, root)
    service.index("repo")
    chunk = service._store.list_workspace("repo")[0]
    with service._store._connect() as connection:
        connection.execute(
            "UPDATE knowledge_embeddings SET dimensions = 2 WHERE chunk_id = ?",
            (chunk.chunk_id,),
        )

    results = service.search("repo", "persisted results", mode="semantic")

    assert all(item.chunk.chunk_id != chunk.chunk_id for item in results)
