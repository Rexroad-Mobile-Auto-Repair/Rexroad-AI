from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.plans.models import PlanCreate, PlanStep, ProjectPlan, StepStatus


class PlanService:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, workspace TEXT, goal TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS plan_steps (
                id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, position INTEGER NOT NULL, title TEXT NOT NULL,
                status TEXT NOT NULL, metadata_json TEXT NOT NULL, started_at TEXT, completed_at TEXT,
                reference TEXT, UNIQUE(plan_id, position)
            )""")

    def create(self, request: PlanCreate) -> ProjectPlan:
        if not request.steps:
            raise ValueError("plan requires at least one step")
        now = datetime.now(UTC)
        plan = ProjectPlan(id=str(uuid4()), scope=request.scope, workspace=request.workspace, goal=request.goal,
                           steps=[PlanStep(position=i, title=step.title, metadata=step.metadata) for i, step in enumerate(request.steps)],
                           created_at=now, updated_at=now, metadata=request.metadata)
        with sqlite3.connect(self._database_path) as connection:
            self._insert(connection, plan)
        return plan

    def get(self, plan_id: str, scope: str | None = None) -> ProjectPlan | None:
        with sqlite3.connect(self._database_path) as connection:
            plan = self._read_plan(connection, plan_id, scope)
        return plan

    def list(self, scope: str, limit: int = 50) -> list[ProjectPlan]:
        if not scope.strip() or limit < 1 or limit > 100:
            raise ValueError("invalid plan scope or limit")
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute("SELECT id FROM plans WHERE scope = ? ORDER BY created_at, id LIMIT ?", (scope, limit)).fetchall()
            return [self._read_plan(connection, row[0]) for row in rows]

    def transition(self, plan_id: str, step_id: str, status: StepStatus, scope: str | None = None, reference: str | None = None) -> ProjectPlan | None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan = self._read_plan(connection, plan_id, scope)
            if plan is None: return None
            if plan.status in {"completed", "failed", "cancelled"}:
                raise ValueError("cannot mutate a terminal plan")
            step = next((item for item in plan.steps if item.id == step_id), None)
            if step is None or not self._allowed(step.status, status):
                raise ValueError("invalid plan step transition")
            now = datetime.now(UTC)
            step.status = status
            step.reference = reference or step.reference
            if status == "in_progress": step.started_at = step.started_at or now
            if status in {"completed", "failed", "skipped"}: step.completed_at = now
            plan.status = "completed" if all(item.status in {"completed", "skipped"} for item in plan.steps) else "failed" if any(item.status == "failed" for item in plan.steps) else "active"
            plan.updated_at = now
            self._replace(connection, plan)
            return plan

    def cancel(self, plan_id: str, scope: str | None = None) -> ProjectPlan | None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan = self._read_plan(connection, plan_id, scope)
            if plan is None: return None
            if plan.status != "active":
                raise ValueError("cannot cancel a terminal plan")
            plan.status = "cancelled"
            plan.updated_at = datetime.now(UTC)
            self._replace(connection, plan)
            return plan

    def next_step(self, plan_id: str, scope: str | None = None) -> PlanStep | None:
        plan = self.get(plan_id, scope)
        if plan is None: return None
        return next((step for step in plan.steps if step.status == "in_progress"), None) or next((step for step in plan.steps if step.status == "pending"), None)

    @staticmethod
    def _allowed(current: StepStatus, target: StepStatus) -> bool:
        return (current == "pending" and target in {"in_progress", "skipped"}) or (current == "in_progress" and target in {"completed", "failed"})

    @staticmethod
    def _insert(connection: sqlite3.Connection, plan: ProjectPlan) -> None:
        connection.execute("INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (plan.id, plan.scope, plan.workspace, plan.goal, plan.status, plan.created_at.isoformat(), plan.updated_at.isoformat(), json.dumps(plan.metadata, sort_keys=True)))
        for step in plan.steps: connection.execute("INSERT INTO plan_steps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (step.id, plan.id, step.position, step.title, step.status, json.dumps(step.metadata, sort_keys=True), None, None, step.reference))

    @staticmethod
    def _replace(connection: sqlite3.Connection, plan: ProjectPlan) -> None:
        connection.execute("UPDATE plans SET status = ?, updated_at = ? WHERE id = ?", (plan.status, plan.updated_at.isoformat(), plan.id))
        for step in plan.steps: connection.execute("UPDATE plan_steps SET status = ?, metadata_json = ?, started_at = ?, completed_at = ?, reference = ? WHERE id = ?", (step.status, json.dumps(step.metadata, sort_keys=True), step.started_at.isoformat() if step.started_at else None, step.completed_at.isoformat() if step.completed_at else None, step.reference, step.id))

    @staticmethod
    def _read_plan(connection: sqlite3.Connection, plan_id: str, scope: str | None = None) -> ProjectPlan | None:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM plans WHERE id = ?" + (" AND scope = ?" if scope is not None else ""), (plan_id, scope) if scope is not None else (plan_id,)).fetchone()
        if row is None: return None
        steps = [PlanStep(id=item["id"], position=item["position"], title=item["title"], status=item["status"], metadata=json.loads(item["metadata_json"]), started_at=item["started_at"], completed_at=item["completed_at"], reference=item["reference"]) for item in connection.execute("SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY position", (plan_id,)).fetchall()]
        return ProjectPlan(id=row["id"], scope=row["scope"], workspace=row["workspace"], goal=row["goal"], status=row["status"], steps=steps, created_at=row["created_at"], updated_at=row["updated_at"], metadata=json.loads(row["metadata_json"]))
