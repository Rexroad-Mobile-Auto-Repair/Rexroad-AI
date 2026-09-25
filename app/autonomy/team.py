from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.plans.service import PlanService


class AutonomousTeamCoordinator:
    """Durable team metadata above PlanService and bounded parallel execution."""

    MAX_MEMBERS = 4

    def __init__(self, database_path: str | Path, plans: PlanService, continuation: Any) -> None:
        self._path = Path(database_path)
        self._plans = plans
        self._continuation = continuation
        with sqlite3.connect(self._path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS autonomous_teams (team_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, scope TEXT NOT NULL, workspace TEXT, status TEXT NOT NULL, members_json TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT)")

    def create(self, plan_id: str, scope: str, bridge: Any, max_members: int = 2) -> dict[str, Any] | None:
        if max_members < 1 or max_members > self.MAX_MEMBERS:
            raise ValueError("invalid team size")
        plan = self._plans.get(plan_id, scope)
        if plan is None:
            raise ValueError("autonomous plan not found")
        ready = self._continuation.inspect(plan_id, scope)["ready"]
        if len(ready) < 2:
            return None
        members = []
        for step_id in ready[:max_members]:
            step = next(item for item in plan.steps if item.id == step_id)
            spec = bridge.build_spec(plan, step)
            if spec.mutation_required or spec.worker not in {"direct", "code_analyst", "researcher", "verifier"}:
                continue
            members.append({"member_id": str(uuid4()), "worker": spec.worker, "task_ids": [step_id], "status": "assigned", "result_refs": []})
        if len(members) < 2:
            return None
        now = datetime.now(UTC).isoformat()
        team = {"team_id": str(uuid4()), "plan_id": plan_id, "scope": scope, "workspace": plan.workspace, "status": "active", "members": members, "metadata": {"max_members": max_members}, "created_at": now, "completed_at": None}
        with sqlite3.connect(self._path) as db:
            db.execute("INSERT INTO autonomous_teams VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (team["team_id"], plan_id, scope, plan.workspace, "active", json.dumps(members), json.dumps(team["metadata"]), now, None))
        return team

    def get(self, team_id: str, scope: str) -> dict[str, Any] | None:
        with sqlite3.connect(self._path) as db:
            row = db.execute("SELECT team_id, plan_id, scope, workspace, status, members_json, metadata_json, created_at, completed_at FROM autonomous_teams WHERE team_id=? AND scope=?", (team_id, scope)).fetchone()
        if row is None:
            return None
        return {"team_id": row[0], "plan_id": row[1], "scope": row[2], "workspace": row[3], "status": row[4], "members": json.loads(row[5]), "metadata": json.loads(row[6]), "created_at": row[7], "completed_at": row[8]}

    def run(self, team_id: str, scope: str, bridge: Any, authorizations: dict[str, Any], max_steps: int = 1) -> dict[str, Any]:
        team = self.get(team_id, scope)
        if team is None:
            raise ValueError("team not found")
        result = self._continuation.continue_parallel(plan_id=team["plan_id"], scope=scope, bridge=bridge, authorizations=authorizations, max_steps=max_steps, max_concurrency=len(team["members"]))
        members = team["members"]
        by_task = {task_id: item for item in members for task_id in item["task_ids"]}
        for item in result["results"]:
            member = by_task.get(item["step_id"])
            if member is not None:
                member["status"] = item["status"]
                member["result_refs"] = [item.get("trace_id")] if item.get("trace_id") else []
        status = "completed" if result["plan_status"] == "completed" else "active"
        completed_at = datetime.now(UTC).isoformat() if status == "completed" else None
        with sqlite3.connect(self._path) as db:
            db.execute("UPDATE autonomous_teams SET status=?, members_json=?, completed_at=? WHERE team_id=? AND scope=?", (status, json.dumps(members), completed_at, team_id, scope))
        return {"team": self.get(team_id, scope), "execution": result}
