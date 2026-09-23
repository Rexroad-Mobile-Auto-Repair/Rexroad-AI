from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.coding_actions import SupervisorCodingActionService
from app.coding_guidance import CodingGuidanceService
from app.coding_jobs import CodingJobService
from app.coding_proposals import CodingProposalService
from app.coding_workflows import CodingWorkflowService
from app.journal.store import ActionJournal
from app.plans.bridge import TrustedExecutionBridge
from app.plans.execution import PlanExecutionCoordinator
from app.plans.service import PlanService
from app.plans.specs import ExecutionSpecService
from app.plans.traces import ExecutionTraceService
from app.policy.workspaces import WorkspaceRegistry
from app.subagents import SubAgentService
from app.tools.factory import build_tool_registry
from app.tools.filesystem import ReadOnlyFilesystem
from app.tools.git import ReadOnlyGit


def _git_workspace(root: Path) -> None:
    (root / "app").mkdir()
    (root / "tests").mkdir()
    (root / "app/example.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "app/helpers.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (root / "tests/test_example.py").write_text("from app.example import VALUE\n\ndef test_example():\n    assert VALUE == 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "initial"], check=True)


def _services(tmp_path: Path, workspace: Path):
    database = tmp_path / "state.db"
    registry = WorkspaceRegistry({"fixture": workspace})
    filesystem = ReadOnlyFilesystem(registry)
    git = ReadOnlyGit(registry)
    plans = PlanService(database)
    tools = build_tool_registry(filesystem, git, database_path=str(database))
    specs = ExecutionSpecService(database, plans, tools)
    journal = ActionJournal(database)
    agents = SubAgentService(database, providers=None, tools=tools)
    bridge = TrustedExecutionBridge(specs, tools)
    executor = PlanExecutionCoordinator(plans, tools, journal)
    workflows = CodingWorkflowService(database, registry, git, agents, plans, specs, bridge, executor, tools)
    proposals = CodingProposalService(database, workflows, registry, git)
    jobs = CodingJobService(workflows, proposals, specs, ExecutionTraceService(journal), tools)
    actions = SupervisorCodingActionService(jobs, workflows, proposals, tools)
    guidance = CodingGuidanceService(jobs)
    return database, registry, workflows, proposals, jobs, actions, guidance


def _install(monkeypatch, bundle) -> TestClient:
    _, _, workflows, proposals, jobs, actions, guidance = bundle
    monkeypatch.setattr(main, "coding_workflow_service", workflows)
    monkeypatch.setattr(main, "coding_proposal_service", proposals)
    monkeypatch.setattr(main, "coding_job_service", jobs)
    monkeypatch.setattr(main, "coding_action_service", actions)
    monkeypatch.setattr(main, "coding_guidance_service", guidance)
    return TestClient(main.app)


def test_real_guidance_lifecycle_persists_and_executes(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _git_workspace(workspace)
    bundle = _services(tmp_path, workspace)
    client = _install(monkeypatch, bundle)
    from app.coding_workflows import CodingWorkflowCreate
    workflow = bundle[2].create(CodingWorkflowCreate(scope="scope", workspace="fixture", instruction="inspect source code and apply the reviewed changes"))
    wid = workflow.workflow_id

    assert client.get(f"/supervisor-coding-workflows/{wid}/guidance", params={"scope": "scope"}).json()["next_action"] == "start_analysis"
    response = client.post(f"/supervisor-coding-workflows/{wid}/action", json={"action": "start_analysis", "scope": "scope"})
    assert response.status_code == 200, response.text
    assert response.json()["job"]["next_action"]["action"] == "review_analysis", response.text
    assert client.get(f"/supervisor-coding-workflows/{wid}/guidance", params={"scope": "scope"}).json()["next_action"] == "review_analysis"
    analyst_id = response.json()["job"]["analyst_task_id"]
    assert bundle[2].agents.get_review(analyst_id, "scope") is not None
    review_analysis = client.post(f"/supervisor-coding-workflows/{wid}/action", json={"action": "review_analysis", "scope": "scope", "decision": "accept", "reviewer_session_id": "supervisor"})
    assert review_analysis.status_code == 200, review_analysis.text
    assert review_analysis.json()["job"]["next_action"]["action"] == "create_proposal", review_analysis.text

    proposal = {"changes": [
        {"relative_path": "app/example.py", "expected_text": "VALUE = 1\n", "replacement": "VALUE = 1\n# reviewed\n"},
        {"relative_path": "app/helpers.py", "expected_text": "def helper():\n    return 1\n", "replacement": "def helper():\n    return 2\n"},
    ], "checks": [{"check_id": "pytest", "targets": ["tests/test_example.py"]}], "summary": "bounded fixture change"}
    created = client.post(f"/supervisor-coding-workflows/{wid}/action", json={"action": "create_proposal", "scope": "scope", "proposal": proposal})
    assert created.status_code == 200, created.text
    guidance = client.get(f"/supervisor-coding-workflows/{wid}/guidance", params={"scope": "scope"}).json()
    assert guidance["next_action"] == "review_proposal"
    assert "VALUE = 1" not in str(guidance)

    assert client.post(f"/supervisor-coding-workflows/{wid}/action", json={"action": "review_proposal", "scope": "scope", "decision": "accept"}).status_code == 200
    assert client.get(f"/supervisor-coding-workflows/{wid}/guidance", params={"scope": "scope"}).json()["next_action"] == "convert_proposal"
    converted_action = client.post(f"/supervisor-coding-workflows/{wid}/action", json={"action": "convert_proposal", "scope": "scope"})
    assert converted_action.status_code == 200, converted_action.text
    converted = client.get(f"/supervisor-coding-workflows/{wid}/guidance", params={"scope": "scope"}).json()
    assert converted["next_action"] == "review_specs", converted_action.text
    assert converted["action_preview"] == {"patch_count": 2, "check_count": 1}
    assert client.post(f"/supervisor-coding-workflows/{wid}/action", json={"action": "review_specs", "scope": "scope", "decision": "accept"}).status_code == 200
    assert client.get(f"/supervisor-coding-workflows/{wid}/guidance", params={"scope": "scope"}).json()["next_action"] == "request_patch_approval"
    assert client.post(f"/supervisor-coding-workflows/{wid}/action", json={"action": "request_patch_approval", "scope": "scope"}).status_code == 200
    approval_guidance = client.get(f"/supervisor-coding-workflows/{wid}/guidance", params={"scope": "scope"}).json()
    assert approval_guidance["next_action"] == "review_patch_approvals"
    assert len(approval_guidance["action_preview"]["pending_request_ids"]) == 2

    first = approval_guidance["action_preview"]["pending_request_ids"][0]
    assert client.post(f"/supervisor-coding-workflows/{wid}/action", json={"action": "review_patch_approvals", "scope": "scope", "approval_request_id": first, "decision": "accept"}).status_code == 200
    partial = client.get(f"/supervisor-coding-workflows/{wid}/guidance", params={"scope": "scope"}).json()
    assert partial["next_action"] == "review_patch_approvals"
    assert partial["action_preview"]["approved_count"] == 1

    after_restart = _install(monkeypatch, _services(tmp_path, workspace)).get(f"/supervisor-coding-workflows/{wid}/guidance", params={"scope": "scope"}).json()
    assert after_restart["next_action"] == "review_patch_approvals"
    assert after_restart["action_preview"]["approved_count"] == 1

    # Approval review is performed by the original in-process registry; the
    # restart above proves the persisted review state remains visible.
    original_client = _install(monkeypatch, bundle)
    for request_id in after_restart["action_preview"]["pending_request_ids"]:
        assert original_client.post(f"/supervisor-coding-workflows/{wid}/action", json={"action": "review_patch_approvals", "scope": "scope", "approval_request_id": request_id, "decision": "accept"}).status_code == 200
    restarted = _install(monkeypatch, _services(tmp_path, workspace))
    ready = restarted.get(f"/supervisor-coding-workflows/{wid}/guidance", params={"scope": "scope"}).json()
    assert ready["next_action"] == "execute_patches"
    assert ready["high_impact"] is True
    executed = restarted.post(f"/supervisor-coding-workflows/{wid}/action", json={"action": "execute_patches", "scope": "scope"})
    assert executed.status_code == 200, executed.text
    assert (workspace / "app/example.py").read_text(encoding="utf-8") == "VALUE = 1\n# reviewed\n"
    assert (workspace / "app/helpers.py").read_text(encoding="utf-8") == "def helper():\n    return 2\n"
    post_patch = restarted.get(f"/supervisor-coding-workflows/{wid}/guidance", params={"scope": "scope"}).json()
    assert post_patch["next_action"] == "execute_checks"
    assert post_patch["high_impact"] is False
    checked = restarted.post(f"/supervisor-coding-workflows/{wid}/action", json={"action": "execute_checks", "scope": "scope"})
    assert checked.status_code == 200, checked.text
    check_guidance = restarted.get(f"/supervisor-coding-workflows/{wid}/guidance", params={"scope": "scope"}).json()
    assert check_guidance["next_action"] == "start_verifier"
    started = restarted.post(f"/supervisor-coding-workflows/{wid}/action", json={"action": "start_verifier", "scope": "scope"})
    assert started.status_code == 200, started.text
    verifier_guidance = restarted.get(f"/supervisor-coding-workflows/{wid}/guidance", params={"scope": "scope"}).json()
    assert verifier_guidance["next_action"] == "review_verifier"
    assert verifier_guidance["action_preview"]["review_status"] == "pending"
    accepted = restarted.post(f"/supervisor-coding-workflows/{wid}/action", json={"action": "review_verifier", "scope": "scope", "decision": "accept", "reviewer_session_id": "supervisor"})
    assert accepted.status_code == 200, accepted.text
    terminal = restarted.get(f"/supervisor-coding-workflows/{wid}/guidance", params={"scope": "scope"}).json()
    assert terminal["next_action"] == "none"
    assert terminal["action_available"] is False
