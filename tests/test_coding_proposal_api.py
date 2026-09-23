import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.coding_proposals import CodingProposalService, ProposalCreate, ProposedChange
from app.coding_workflows import CodingWorkflowCreate
from app.policy.workspaces import WorkspaceRegistry
from app.subagents import SubAgentResult
from tests.test_coding_workflows import _service


class _ProposalAPI:
    def revision_candidate(self, workflow_id: str, scope: str) -> dict:
        if scope != "scope":
            raise ValueError("missing")
        return {"workflow_id": workflow_id, "parent_proposal_id": "parent", "revision_number": 2, "task_id": "task", "review_status": "accepted", "note": "n", "materialized_proposal_id": None, "summary": "safe", "candidate": {"changes": [], "checks": [], "summary": "safe"}}

    def materialize_revision_candidate(self, workflow_id: str, scope: str):
        return type("Proposal", (), {"model_dump": lambda self: {"proposal_id": "child", "workflow_id": workflow_id, "scope": scope}})()


def test_revision_candidate_api_is_read_only_and_scoped(monkeypatch):
    monkeypatch.setattr(main, "coding_proposal_service", _ProposalAPI())
    client = TestClient(main.app)
    response = client.get("/supervisor-coding-workflows/wf/proposal/revision-candidate", params={"scope": "scope"})
    assert response.status_code == 200
    assert response.json()["review_status"] == "accepted"
    assert client.get("/supervisor-coding-workflows/wf/proposal/revision-candidate", params={"scope": "other"}).status_code == 409


def test_revision_materialization_api_returns_safe_error(monkeypatch):
    class Failing(_ProposalAPI):
        def materialize_revision_candidate(self, workflow_id: str, scope: str):
            raise ValueError("cannot")
    monkeypatch.setattr(main, "coding_proposal_service", Failing())
    client = TestClient(main.app)
    response = client.post("/supervisor-coding-workflows/wf/proposal/revision-candidate/materialize", params={"scope": "scope"})
    assert response.status_code == 409
    assert "sqlite" not in response.text and "traceback" not in response.text


def _real_service(tmp_path: Path):
    workflows = _service(tmp_path)
    workflow = workflows.create(CodingWorkflowCreate(scope="scope", workspace="ws", instruction="revise"))
    workflows._save(workflow.model_copy(update={"status": "awaiting_analysis_review", "analyst_task_id": "analyst"}))
    (tmp_path / "target.txt").write_text("before", encoding="utf-8")
    service = CodingProposalService(tmp_path / "state.db", workflows, WorkspaceRegistry({"ws": tmp_path}), workflows.git)
    parent = service.create(workflow.workflow_id, "scope", ProposalCreate(changes=[ProposedChange(relative_path="target.txt", expected_text="before", replacement="after")]))
    parent = service.review(parent.proposal_id, "scope", "rejected", "supervisor", "keep it narrow")
    result = SubAgentResult(task_id="revision-task", worker_profile="code_analyst", status="completed", summary=json.dumps({"proposal": {"changes": [{"relative_path": "target.txt", "expected_text": "before", "replacement": "revised"}], "checks": [], "summary": "safe"}}), started_at=datetime.now(UTC), completed_at=datetime.now(UTC))
    with sqlite3.connect(tmp_path / "state.db") as db:
        db.execute("CREATE TABLE IF NOT EXISTS sub_agent_tasks (task_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, result_json TEXT, created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS sub_agent_reviews (task_id TEXT PRIMARY KEY, scope TEXT NOT NULL, status TEXT NOT NULL, reviewer_session_id TEXT, note TEXT, reviewed_at TEXT NOT NULL)")
        task = {"task_id": "revision-task", "worker_profile": "code_analyst", "scope": "scope", "workspace": "ws", "instruction": "revise", "allowed_tools": [], "status": "completed", "parent_session_id": None, "plan_id": None, "step_id": None, "created_at": datetime.now(UTC).isoformat()}
        db.execute("INSERT INTO sub_agent_tasks VALUES (?, ?, ?, ?)", ("revision-task", json.dumps(task), result.model_dump_json(), datetime.now(UTC).isoformat()))
        db.execute("INSERT INTO sub_agent_reviews VALUES (?, ?, 'accepted', 'supervisor', NULL, ?)", ("revision-task", "scope", datetime.now(UTC).isoformat()))
        db.execute("INSERT INTO proposal_revision_handoffs VALUES (?, ?, ?, ?, ?, NULL)", ("revision-task", "scope", workflow.workflow_id, parent.proposal_id, "keep it narrow"))

    class Agents:
        def get(self, task_id):
            with sqlite3.connect(tmp_path / "state.db") as db:
                row = db.execute("SELECT payload_json, result_json FROM sub_agent_tasks WHERE task_id=?", (task_id,)).fetchone()
            return (type("Task", (), {"task_id": task_id})(), SubAgentResult.model_validate_json(row[1])) if row else None
        def get_review(self, task_id, scope):
            return type("Review", (), {"status": "accepted"})()
    workflows.agents = Agents()
    return service, workflows, workflow.workflow_id, parent.proposal_id


def test_real_api_revision_happy_path_and_replay(tmp_path: Path, monkeypatch):
    service, _workflows, workflow_id, parent_id = _real_service(tmp_path)
    monkeypatch.setattr(main, "coding_proposal_service", service)
    client = TestClient(main.app)
    candidate = client.get(f"/supervisor-coding-workflows/{workflow_id}/proposal/revision-candidate", params={"scope": "scope"})
    assert candidate.status_code == 200
    created = client.post(f"/supervisor-coding-workflows/{workflow_id}/proposal/revision-candidate/materialize", params={"scope": "scope"})
    assert created.status_code == 200
    child_id = created.json()["proposal_id"]
    assert created.json()["revision_number"] == 2
    replay = client.post(f"/supervisor-coding-workflows/{workflow_id}/proposal/revision-candidate/materialize", params={"scope": "scope"})
    assert replay.status_code == 200 and replay.json()["proposal_id"] == child_id
    history = client.get(f"/supervisor-coding-workflows/{workflow_id}/proposals", params={"scope": "scope"}).json()
    assert [item["revision_number"] for item in history] == [1, 2]
    assert service.get(parent_id, "scope").superseded_by_proposal_id == child_id
    assert (tmp_path / "target.txt").read_text(encoding="utf-8") == "before"


def test_real_api_candidate_wrong_scope_and_stale_workspace_are_safe(tmp_path: Path, monkeypatch):
    service, _workflows, workflow_id, _ = _real_service(tmp_path)
    monkeypatch.setattr(main, "coding_proposal_service", service)
    client = TestClient(main.app)
    assert client.get(f"/supervisor-coding-workflows/{workflow_id}/proposal/revision-candidate", params={"scope": "other"}).status_code == 409
    (tmp_path / "target.txt").write_text("external", encoding="utf-8")
    assert client.post(f"/supervisor-coding-workflows/{workflow_id}/proposal/revision-candidate/materialize", params={"scope": "scope"}).status_code == 409
