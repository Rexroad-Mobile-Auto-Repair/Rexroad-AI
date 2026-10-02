import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.autonomy.service import AutonomousContinuationService
from app.autonomy.team import AutonomousTeamCoordinator
from app.plans.models import PlanCreate, PlanStepCreate
from app.plans.service import PlanService


def interrupted_plan(tmp_path):
    plans = PlanService(tmp_path / "state.db")
    metadata = {"worker": "code_analyst", "tool_category": "read", "mutation_required": False}
    plan = plans.create(PlanCreate(scope="s", goal="Read-only review", metadata={"read_only": True},
        steps=[PlanStepCreate(title="done", metadata=metadata), PlanStepCreate(title="interrupted", metadata=metadata)]))
    plans.transition(plan.id, plan.steps[0].id, "in_progress", "s")
    plans.transition(plan.id, plan.steps[0].id, "completed", "s", "saved-trace")
    plans.transition(plan.id, plan.steps[1].id, "in_progress", "s")
    return plans, plans.get(plan.id, "s")


def test_recovery_preserves_completed_work_and_records_interrupted_worker(tmp_path):
    plans, before = interrupted_plan(tmp_path)
    with sqlite3.connect(tmp_path / "state.db") as db:
        db.executescript("CREATE TABLE sub_agent_tasks(task_id TEXT, payload_json TEXT); CREATE TABLE supervisor_dispatch_audits(task_id TEXT, scope TEXT, status TEXT, safe_reason TEXT, completed_at TEXT); CREATE TABLE approvals(status TEXT);")
        db.execute("INSERT INTO approvals VALUES ('consumed')")
        db.execute("INSERT INTO sub_agent_tasks VALUES ('worker', ?)", (json.dumps({"scope": "s", "plan_id": before.id, "step_id": before.steps[1].id, "status": "running"}),))
        db.execute("INSERT INTO supervisor_dispatch_audits VALUES ('worker','s','running',NULL,NULL)")
    recovered = plans.recover_read_only(before.id, "s")
    assert recovered.steps[0] == before.steps[0]
    assert recovered.steps[1].status == "pending"
    assert recovered.steps[1].metadata["recovery_count"] == 1
    assert recovered.steps[1].metadata["interrupted_attempts"][0]["started_at"] == before.steps[1].started_at.isoformat()
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert json.loads(db.execute("SELECT payload_json FROM sub_agent_tasks").fetchone()[0])["status"] == "failed"
        assert db.execute("SELECT safe_reason FROM supervisor_dispatch_audits").fetchone()[0] == "interrupted_runtime"
        assert db.execute("SELECT status FROM approvals").fetchone()[0] == "consumed"
    assert plans.recover_read_only(before.id, "s") == recovered
    plans.transition(before.id, before.steps[1].id, "in_progress", "s")
    with pytest.raises(ValueError, match="retry limit"):
        plans.recover_read_only(before.id, "s")


def test_recovery_rejects_mutation_and_wrong_scope(tmp_path):
    plans, plan = interrupted_plan(tmp_path)
    with pytest.raises(ValueError, match="read-only"):
        plans.recover_read_only(plan.id, "other")
    with sqlite3.connect(tmp_path / "state.db") as db:
        metadata = {**plan.steps[1].metadata, "mutation_required": True}
        db.execute("UPDATE plan_steps SET metadata_json=? WHERE id=?", (json.dumps(metadata), plan.steps[1].id))
    with pytest.raises(ValueError, match="mutation"):
        plans.recover_read_only(plan.id, "s")
    assert plans.get(plan.id, "s").steps[1].status == "in_progress"


def test_routed_resume_does_not_reset_a_live_request(tmp_path):
    plans, plan = interrupted_plan(tmp_path)
    service = AutonomousContinuationService(plans, None)
    entered, release = Event(), Event()
    def inner(**kwargs):
        entered.set()
        assert release.wait(5)
        return {"done": True}
    service._continue_routed = inner
    kwargs = {"plan_id": plan.id, "scope": "s", "bridge": None, "teams": None, "authorizations": {}}
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(service.continue_routed, **kwargs)
        assert entered.wait(5)
        repeated = service.continue_routed(**kwargs)
        assert repeated["waiting"] == "already_running"
        assert plans.get(plan.id, "s").steps[1].metadata["recovery_count"] == 1
        release.set()
        assert running.result() == {"done": True}


def test_team_restores_completed_member_and_saved_evidence(tmp_path):
    plans, plan = interrupted_plan(tmp_path)
    contract = {"task_id": "completed-worker", "worker_type": "code_analyst", "status": "completed", "summary": "Saved source result"}
    team = AutonomousTeamCoordinator(tmp_path / "state.db", plans, None,
        result_loader=lambda pid, scope: [{"step_id": plan.steps[0].id, "contract": contract}])
    members = [{"task_ids": [step.id], "status": "assigned", "result_refs": []} for step in plan.steps]
    with sqlite3.connect(tmp_path / "state.db") as db:
        db.execute("INSERT INTO autonomous_teams VALUES ('t', ?, 's', NULL, 'active', ?, '{}', 'now', NULL)", (plan.id, json.dumps(members)))
    plans.recover_read_only(plan.id, "s")
    team.restore_completed(plan.id, "s")
    saved = team.get("t", "s")
    assert saved["members"][0]["status"] == "completed"
    assert saved["members"][0]["result_refs"] == ["saved-trace"]
    assert saved["metadata"]["worker_results"] == [contract]
    team.restore_completed(plan.id, "s")
    assert team.get("t", "s") == saved


def test_recovered_team_finishes_remaining_member_without_rerunning_completed(tmp_path):
    from tests.test_autonomous_team import build

    plans, tools, continuation, bridge, teams = build(tmp_path)
    metadata = {"worker": "direct", "tool_category": "repo_map", "mutation_required": False}
    plan = continuation.create_goal(PlanCreate(scope="s", workspace="repo", goal="inspect", metadata={"read_only": True},
        steps=[PlanStepCreate(title="first", metadata=metadata), PlanStepCreate(title="second", metadata=metadata)]))
    auth = tools.authorize("workspace.repo_map", "s")
    team = teams.create(plan.id, "s", bridge, authorizations={"workspace.repo_map": auth})
    plans.transition(plan.id, plan.steps[0].id, "in_progress", "s")
    plans.transition(plan.id, plan.steps[0].id, "completed", "s", "original-trace")
    plans.transition(plan.id, plan.steps[1].id, "in_progress", "s")
    result = continuation.continue_routed(plan_id=plan.id, scope="s", bridge=bridge, teams=teams,
        authorizations={"workspace.repo_map": auth})
    assert result["execution"]["attempted"] == [plan.steps[1].id]
    assert result["execution"]["plan_status"] == "completed"
    assert result["team"]["team_id"] == team["team_id"]
    assert result["team"]["status"] == "completed"
    assert plans.get(plan.id, "s").steps[0].reference == "original-trace"


def test_team_results_survive_interrupted_comparison(tmp_path):
    from tests.test_autonomous_team import build

    _plans, tools, continuation, bridge, teams = build(tmp_path)
    plan = continuation.create_goal(PlanCreate(scope="s", workspace="repo", goal="read", steps=[
        PlanStepCreate(title="one", metadata={"worker": "direct", "tool_category": "repo_map"}),
        PlanStepCreate(title="two", metadata={"worker": "direct", "tool_category": "repo_map"})]))
    auth = tools.authorize("workspace.repo_map", "s")
    team = teams.create(plan.id, "s", bridge, authorizations={"workspace.repo_map": auth})
    class BrokenComparison:
        def reconcile(self, *args, **kwargs):
            raise RuntimeError("interrupted comparison")
    teams._reconciler = BrokenComparison()
    with pytest.raises(RuntimeError, match="interrupted comparison"):
        teams.run(team["team_id"], "s", bridge, {"workspace.repo_map": auth})
    saved = teams.get(team["team_id"], "s")
    assert saved["status"] == "completed"
    assert len(saved["metadata"]["worker_results"]) == 2
    assert saved["metadata"]["synthesis"]["reconciliation_status"] != "completed"
