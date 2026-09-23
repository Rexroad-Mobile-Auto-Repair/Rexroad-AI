from app.project_history import ProjectSnapshotStore, ProjectStateHistoryService
from app.project_state import ProjectState


def test_snapshot_persists_and_compares(tmp_path):
    state = ProjectState(workspace="w", scope="s", available=True, branch="main", head="a", clean=True, changed_files=[], active_plans=[], next_steps=[], unresolved_tasks=[], recent_traces=[], observed_at="2026-01-01T00:00:00Z")
    store = ProjectSnapshotStore(tmp_path / "x.sqlite3")
    first = store.create(state)
    loaded = ProjectSnapshotStore(tmp_path / "x.sqlite3").get(first.id, "w", "s")
    assert loaded and loaded.state.head == "a"
    changed = state.model_copy(update={"head": "b"})
    second = store.create(changed)
    comparison = ProjectStateHistoryService(None, store).compare(first, second)
    assert "git" in comparison.changes
