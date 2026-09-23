from pathlib import Path

from app.memory.models import MemoryCreate, MemoryUpdate
from app.memory.service import MemoryService
from app.memory.store import MemoryStore


def service(tmp_path: Path) -> MemoryService:
    return MemoryService(MemoryStore(tmp_path / "journal.sqlite3"))


def test_memory_lifecycle_scope_filters_and_restart(tmp_path):
    first = service(tmp_path)
    decision = first.create(MemoryCreate(scope="workspace_a", category="decision", content="Use implementation A", provenance="user_explicit"))
    first.create(MemoryCreate(scope="workspace_b", category="decision", content="Use implementation B"))
    assert first.get(decision.id) == decision
    assert [item.content for item in first.list("workspace_a")] == ["Use implementation A"]
    assert first.search("workspace_a", "implementation A")[0].id == decision.id
    assert first.format_for_context([decision]).startswith("[Project Memory]")
    restarted = service(tmp_path)
    assert restarted.get(decision.id).content == "Use implementation A"
    assert restarted.list("workspace_b")[0].content == "Use implementation B"


def test_update_resolve_delete_and_metadata(tmp_path):
    memory = service(tmp_path)
    record = memory.create(MemoryCreate(scope="p", category="task", content="Finish ingestion", metadata={"priority": 1}))
    updated = memory.update(record.id, MemoryUpdate(content="Finish PDF ingestion", status="resolved"))
    assert updated.status == "resolved"
    assert memory.list("p", status="active") == []
    assert memory.delete(record.id)
    assert memory.get(record.id) is None
    assert not memory.delete("missing")


def test_deterministic_categories_and_safe_validation(tmp_path):
    memory = service(tmp_path)
    for category in ("fact", "preference", "outcome", "note"):
        memory.create(MemoryCreate(scope="p", category=category, content=category))
    assert [item.category for item in memory.list("p", limit=10)] == ["note", "outcome", "preference", "fact"]
