from pathlib import Path

import pytest

from app.knowledge.service import KnowledgeService
from app.knowledge.store import KnowledgeStore
from app.policy.workspaces import WorkspaceAccessError, WorkspaceRegistry


def _service(tmp_path: Path) -> tuple[KnowledgeService, WorkspaceRegistry]:
    roots = {name: tmp_path / name for name in ("workspace_a", "workspace_b")}
    for name, root in roots.items():
        root.mkdir()
        (root / "app").mkdir()
        (root / "app" / "service.py").write_text(
            f"def shared_lookup():\n    return '{name}'\n", encoding="utf-8"
        )
    registry = WorkspaceRegistry(roots)
    return KnowledgeService(registry, KnowledgeStore(tmp_path / "index.sqlite3")), registry


def test_registry_lists_multiple_workspaces_without_paths(tmp_path):
    service, registry = _service(tmp_path)
    assert [item.name for item in registry.list()] == ["workspace_a", "workspace_b"]
    assert all(item.available for item in registry.list())
    assert all(str(tmp_path) not in item.model_dump_json() for item in registry.list())
    service.index("workspace_a")


def test_index_and_search_are_workspace_isolated(tmp_path):
    service, _ = _service(tmp_path)
    service.index("workspace_a")
    service.index("workspace_b")
    a = service.search("workspace_a", "shared_lookup", limit=10)
    b = service.search("workspace_b", "shared_lookup", limit=10)
    assert a and b
    assert {item.evidence.workspace for item in a} == {"workspace_a"}
    assert {item.evidence.workspace for item in b} == {"workspace_b"}
    assert a[0].chunk.content != b[0].chunk.content


def test_unknown_and_traversal_workspace_access_fail_safely(tmp_path):
    service, registry = _service(tmp_path)
    with pytest.raises(WorkspaceAccessError):
        service.search("missing", "shared_lookup")
    with pytest.raises(WorkspaceAccessError):
        registry.resolve_path("workspace_a", "../workspace_b/app/service.py")
