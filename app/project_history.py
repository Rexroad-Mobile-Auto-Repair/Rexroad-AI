from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from app.project_state import ProjectState, ProjectStateService


class ProjectSnapshot(BaseModel):
    id: str
    workspace: str
    scope: str
    state: ProjectState
    captured_at: datetime


class ProjectStateComparison(BaseModel):
    from_snapshot: str
    to_snapshot: str
    changes: dict[str, object]


class ProjectSnapshotStore:
    def __init__(self, database_path: str | Path) -> None:
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS project_state_snapshots (id TEXT PRIMARY KEY, workspace TEXT NOT NULL, scope TEXT NOT NULL, state_json TEXT NOT NULL, captured_at TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_project_snapshots_scope ON project_state_snapshots (workspace, scope, captured_at)")

    def create(self, state: ProjectState) -> ProjectSnapshot:
        snapshot = ProjectSnapshot(id=str(uuid4()), workspace=state.workspace, scope=state.scope, state=state, captured_at=datetime.now(UTC))
        payload = json.dumps(state.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        if len(payload) > 100_000:
            raise ValueError("snapshot too large")
        with sqlite3.connect(self._path) as db:
            db.execute("INSERT INTO project_state_snapshots VALUES (?, ?, ?, ?, ?)", (snapshot.id, snapshot.workspace, snapshot.scope, payload, snapshot.captured_at.isoformat()))
        return snapshot

    def get(self, snapshot_id: str, workspace: str, scope: str) -> ProjectSnapshot | None:
        with sqlite3.connect(self._path) as db:
            row = db.execute("SELECT * FROM project_state_snapshots WHERE id=? AND workspace=? AND scope=?", (snapshot_id, workspace, scope)).fetchone()
        if row is None:
            return None
        return ProjectSnapshot(id=row[0], workspace=row[1], scope=row[2], state=ProjectState.model_validate(json.loads(row[3])), captured_at=datetime.fromisoformat(row[4]))

    def list(self, workspace: str, scope: str, limit: int = 20) -> list[ProjectSnapshot]:
        if limit < 1 or limit > 100:
            raise ValueError("invalid limit")
        with sqlite3.connect(self._path) as db:
            rows = db.execute("SELECT * FROM project_state_snapshots WHERE workspace=? AND scope=? ORDER BY captured_at DESC, id DESC LIMIT ?", (workspace, scope, limit)).fetchall()
        return [ProjectSnapshot(id=r[0], workspace=r[1], scope=r[2], state=ProjectState.model_validate(json.loads(r[3])), captured_at=datetime.fromisoformat(r[4])) for r in rows]


class ProjectStateHistoryService:
    def __init__(self, state: ProjectStateService, store: ProjectSnapshotStore) -> None:
        self._state, self._store = state, store

    def snapshot(self, workspace: str, scope: str) -> ProjectSnapshot:
        return self._store.create(self._state.get(workspace, scope))

    def list(self, workspace: str, scope: str, limit: int = 20) -> list[ProjectSnapshot]:
        return self._store.list(workspace, scope, limit)

    def get(self, snapshot_id: str, workspace: str, scope: str) -> ProjectSnapshot | None:
        return self._store.get(snapshot_id, workspace, scope)

    def compare(self, left: ProjectSnapshot, right: ProjectSnapshot) -> ProjectStateComparison:
        a, b = left.state, right.state
        changes: dict[str, object] = {}
        if (a.branch, a.head) != (b.branch, b.head): changes["git"] = {"from": [a.branch, a.head], "to": [b.branch, b.head]}
        if set(a.changed_files) != set(b.changed_files): changes["changed_files"] = {"added": sorted(set(b.changed_files)-set(a.changed_files)), "removed": sorted(set(a.changed_files)-set(b.changed_files))}
        for key in ("active_plans", "next_steps", "unresolved_tasks", "recent_traces"):
            if getattr(a, key) != getattr(b, key): changes[key] = {"from": getattr(a, key), "to": getattr(b, key)}
        return ProjectStateComparison(from_snapshot=left.id, to_snapshot=right.id, changes=changes)
