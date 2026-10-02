from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.autonomy.synthesis import WorkerResultContract, synthesize
from app.plans.service import PlanService


class AutonomousTeamCoordinator:
    """Durable team metadata above PlanService and bounded parallel execution."""

    MAX_MEMBERS = 4

    def __init__(self, database_path: str | Path, plans: PlanService, continuation: Any, reconciler: Any = None, result_loader: Any = None) -> None:
        self._path = Path(database_path)
        self._plans = plans
        self._continuation = continuation
        self._reconciler = reconciler
        self._result_loader = result_loader
        with sqlite3.connect(self._path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS autonomous_teams (team_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, scope TEXT NOT NULL, workspace TEXT, status TEXT NOT NULL, members_json TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT)")

    def create(self, plan_id: str, scope: str, bridge: Any, max_members: int = 2, authorizations: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if max_members < 1 or max_members > self.MAX_MEMBERS:
            raise ValueError("invalid team size")
        plan = self._plans.get(plan_id, scope)
        if plan is None:
            raise ValueError("autonomous plan not found")
        ready = self._continuation.inspect(plan_id, scope)["ready"]
        if len(ready) < 2:
            return None
        members = []
        selected = []
        for step_id in ready:
            step = next(item for item in plan.steps if item.id == step_id)
            spec = bridge.build_spec(plan, step)
            if spec.mutation_required or spec.worker not in {"direct", "code_analyst", "test_analyst", "architecture_analyst", "security_analyst", "researcher", "verifier"}:
                continue
            if authorizations is not None:
                if spec.worker in {"researcher", "verifier"}:
                    continue
                _planned, execution = bridge.resolve(plan, step, authorizations)
                if execution is None:
                    continue
            if any(self._continuation._conflicts(spec, other) for other in selected):
                continue
            selected.append(spec)
            members.append({"member_id": str(uuid4()), "worker": spec.worker, "task_ids": [step_id], "status": "assigned", "result_refs": []})
            if len(members) == max_members:
                break
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

    def get_for_plan(self, plan_id: str, scope: str) -> dict[str, Any] | None:
        with sqlite3.connect(self._path) as db:
            row = db.execute("SELECT team_id FROM autonomous_teams WHERE plan_id=? AND scope=? ORDER BY created_at DESC LIMIT 1", (plan_id, scope)).fetchone()
        return self.get(row[0], scope) if row else None

    def list_for_plan(self, plan_id: str, scope: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self._path) as db:
            rows = db.execute("SELECT team_id FROM autonomous_teams WHERE plan_id=? AND scope=? ORDER BY created_at, team_id", (plan_id, scope)).fetchall()
        return [team for row in rows if (team := self.get(row[0], scope)) is not None]

    def reconcile_saved(self, team_id: str, scope: str) -> dict[str, Any]:
        team = self.get(team_id, scope)
        if team is None or team["status"] not in {"completed", "failed"} or self._reconciler is None:
            raise ValueError("saved team comparison unavailable")
        metadata = team["metadata"]
        results = [WorkerResultContract.model_validate(item) for item in metadata.get("worker_results", [])]
        metadata["synthesis"] = self._reconciler.reconcile(results, scope=scope).model_dump()
        with sqlite3.connect(self._path) as db:
            db.execute("UPDATE autonomous_teams SET metadata_json=? WHERE team_id=? AND scope=?", (json.dumps(metadata), team_id, scope))
        return self.get(team_id, scope)

    def restore_completed(self, plan_id: str, scope: str) -> None:
        plan = self._plans.get(plan_id, scope)
        if plan is None:
            raise ValueError("plan not found")
        saved = self._result_loader(plan_id, scope) if self._result_loader else []
        completed = {step.id: step for step in plan.steps if step.status == "completed"}
        for team in self.list_for_plan(plan_id, scope):
            if team["status"] != "active":
                continue
            contracts = {item["task_id"]: item for item in team["metadata"].get("worker_results", [])}
            task_ids = {step_id for member in team["members"] for step_id in member["task_ids"]}
            for member in team["members"]:
                if all(step_id in completed for step_id in member["task_ids"]):
                    member["status"] = "completed"
                    member["result_refs"] = [completed[step_id].reference for step_id in member["task_ids"] if completed[step_id].reference]
            for item in saved:
                if item["step_id"] in task_ids and item["step_id"] in completed:
                    contracts[item["contract"]["task_id"]] = item["contract"]
            team["metadata"]["worker_results"] = list(contracts.values())
            with sqlite3.connect(self._path) as db:
                db.execute("UPDATE autonomous_teams SET members_json=?, metadata_json=? WHERE team_id=? AND scope=?", (json.dumps(team["members"]), json.dumps(team["metadata"]), team["team_id"], scope))

    def run(self, team_id: str, scope: str, bridge: Any, authorizations: dict[str, Any], max_steps: int = 1) -> dict[str, Any]:
        team = self.get(team_id, scope)
        if team is None:
            raise ValueError("team not found")
        result = self._continuation.continue_parallel(plan_id=team["plan_id"], scope=scope, bridge=bridge, authorizations=authorizations, max_steps=max_steps, max_concurrency=len(team["members"]), allowed_step_ids={task_id for member in team["members"] for task_id in member["task_ids"]})
        members = team["members"]
        by_task = {task_id: item for item in members for task_id in item["task_ids"]}
        for item in result["results"]:
            member = by_task.get(item["step_id"])
            if member is not None:
                member["status"] = item["status"]
                member["result_refs"] = [item.get("trace_id")] if item.get("trace_id") else []
        status = "completed" if all(item["status"] == "completed" for item in members) else "active"
        if any(item["status"] == "failed" for item in members):
            status = "failed"
        metadata = team["metadata"]
        contracts = {item["task_id"]: item for item in metadata.get("worker_results", [])}
        for item in result["results"]:
            if item.get("worker_result"):
                contract = WorkerResultContract.model_validate(item["worker_result"])
            else:
                contract = WorkerResultContract(worker_type=item["worker"], task_id=item["step_id"], status=item["status"], summary=item.get("failure_reason", "Read-only plan execution completed."), evidence_refs=[item["trace_id"]] if item.get("trace_id") else [])
            contracts[contract.task_id] = contract.model_dump()
        metadata["worker_results"] = list(contracts.values())
        worker_results = [WorkerResultContract.model_validate(item) for item in contracts.values()]
        # Persist worker outcomes before optional provider-backed comparison.
        # A restart during reconciliation must not lose completed evidence.
        metadata["synthesis"] = synthesize(worker_results).model_dump()
        completed_at = datetime.now(UTC).isoformat() if status == "completed" else None
        with sqlite3.connect(self._path) as db:
            db.execute("UPDATE autonomous_teams SET status=?, members_json=?, metadata_json=?, completed_at=? WHERE team_id=? AND scope=?", (status, json.dumps(members), json.dumps(metadata), completed_at, team_id, scope))
        if self._reconciler:
            metadata["synthesis"] = self._reconciler.reconcile(worker_results, scope=scope).model_dump()
            with sqlite3.connect(self._path) as db:
                db.execute("UPDATE autonomous_teams SET metadata_json=? WHERE team_id=? AND scope=?", (json.dumps(metadata), team_id, scope))
        return {"team": self.get(team_id, scope), "execution": result}
