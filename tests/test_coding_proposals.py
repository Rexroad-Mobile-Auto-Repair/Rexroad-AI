import sqlite3
from pathlib import Path

import pytest

from app.coding_proposals import CodingProposalService, ProposalCreate, ProposedChange
from app.coding_workflows import CodingWorkflowCreate
from app.policy.workspaces import WorkspaceRegistry
from tests.test_coding_workflows import _service


def test_proposal_is_inert_persisted_and_reviewable(tmp_path: Path) -> None:
    (tmp_path / "target.txt").write_text("before", encoding="utf-8")
    workflow_service = _service(tmp_path)
    workflow = workflow_service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="edit"))
    workflow = workflow.model_copy(update={"status": "awaiting_analysis_review", "analyst_task_id": "analyst"})
    workflow_service._save(workflow)
    proposals = CodingProposalService(tmp_path / "state.db", workflow_service, WorkspaceRegistry({"ws": tmp_path}), workflow_service.git)
    proposal = proposals.create(workflow.workflow_id, "s", ProposalCreate(changes=[ProposedChange(relative_path="target.txt", expected_text="before", replacement="after")]))
    assert proposal.status == "ready_for_review"
    assert (tmp_path / "target.txt").read_text(encoding="utf-8") == "before"
    assert proposals.get(proposal.proposal_id, "s").target_hashes["target.txt"]
    accepted = proposals.review(proposal.proposal_id, "s", "accepted", "supervisor")
    assert accepted.status == "accepted"
    assert proposals.get(proposal.proposal_id, "other") is None


def test_proposal_rejects_unsupported_check_and_dirty_target(tmp_path: Path) -> None:
    (tmp_path / "target.txt").write_text("dirty", encoding="utf-8")
    workflow_service = _service(tmp_path, "## main\n M target.txt")
    workflow = workflow_service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="edit"))
    workflow_service._save(workflow.model_copy(update={"status": "awaiting_analysis_review", "analyst_task_id": "analyst"}))
    proposals = CodingProposalService(tmp_path / "state.db", workflow_service, WorkspaceRegistry({"ws": tmp_path}), workflow_service.git)
    with pytest.raises(ValueError, match="dirty"):
        proposals.create(workflow.workflow_id, "s", ProposalCreate(changes=[ProposedChange(relative_path="target.txt", expected_text="dirty", replacement="clean")]))


def test_accepted_proposal_conversion_creates_only_draft_specs(tmp_path: Path) -> None:
    (tmp_path / "target.txt").write_text("before", encoding="utf-8")
    workflow_service = _service(tmp_path)
    workflow = workflow_service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="edit"))
    workflow_service._save(workflow.model_copy(update={"status": "awaiting_analysis_review", "analyst_task_id": "analyst"}))
    proposals = CodingProposalService(tmp_path / "state.db", workflow_service, WorkspaceRegistry({"ws": tmp_path}), workflow_service.git)
    proposal = proposals.create(workflow.workflow_id, "s", ProposalCreate(changes=[ProposedChange(relative_path="target.txt", expected_text="before", replacement="after")]))
    proposals.review(proposal.proposal_id, "s", "accepted", "supervisor")

    class Plan:
        id = "plan"
        def __init__(self):
            self.steps = [type("Step", (), {"id": "step"})()]

    class Plans:
        def create(self, request):
            return Plan()

    class Specs:
        def __init__(self): self.created = []
        def create(self, **kwargs):
            self.created.append(kwargs)
            return type("Spec", (), {"id": f"spec-{len(self.created)}"})()

    workflow_service.plans = Plans()
    workflow_service.specs = Specs()
    converted = proposals.convert(proposal.proposal_id, "s", "ws")
    assert converted.conversion_status == "converted"
    assert converted.patch_spec_ids == ["spec-1"]
    assert converted.status == "accepted"
    assert (tmp_path / "target.txt").read_text(encoding="utf-8") == "before"


def test_spec_review_is_explicit_and_restart_safe(tmp_path: Path) -> None:
    (tmp_path / "target.txt").write_text("before", encoding="utf-8")
    workflow_service = _service(tmp_path)
    workflow = workflow_service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="edit"))
    workflow_service._save(workflow.model_copy(update={"status": "awaiting_analysis_review", "analyst_task_id": "analyst"}))
    proposals = CodingProposalService(tmp_path / "state.db", workflow_service, WorkspaceRegistry({"ws": tmp_path}), workflow_service.git)
    proposal = proposals.create(workflow.workflow_id, "s", ProposalCreate(changes=[ProposedChange(relative_path="target.txt", expected_text="before", replacement="after")]))
    proposal = proposals.review(proposal.proposal_id, "s", "accepted", "supervisor")
    proposal = proposal.model_copy(update={"conversion_status": "converted", "patch_spec_ids": ["spec-1"], "spec_review_status": "pending"})
    proposals._save(proposal)
    assert proposals.spec_review(proposal.proposal_id, "s")["status"] == "pending"
    assert proposals.reject_specs(proposal.proposal_id, "s", "supervisor").spec_review_status == "rejected"
    restarted = CodingProposalService(tmp_path / "state.db", workflow_service, WorkspaceRegistry({"ws": tmp_path}), workflow_service.git)
    assert restarted.get(proposal.proposal_id, "s").spec_review_status == "rejected"


def test_conversion_rolls_back_plan_specs_and_metadata_on_injected_failure(tmp_path: Path) -> None:
    for name in ("one.txt", "two.txt"):
        (tmp_path / name).write_text("before", encoding="utf-8")
    workflow_service = _service(tmp_path)
    workflow = workflow_service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="edit"))
    workflow_service._save(workflow.model_copy(update={"status": "awaiting_analysis_review", "analyst_task_id": "analyst"}))
    db_path = tmp_path / "state.db"
    proposals = CodingProposalService(db_path, workflow_service, WorkspaceRegistry({"ws": tmp_path}), workflow_service.git)
    proposal = proposals.create(workflow.workflow_id, "s", ProposalCreate(changes=[
        ProposedChange(relative_path="one.txt", expected_text="before", replacement="after"),
        ProposedChange(relative_path="two.txt", expected_text="before", replacement="after"),
    ], checks=[{"check_id": "git_diff_check"}]))
    proposals.review(proposal.proposal_id, "s", "accepted", "supervisor")
    with sqlite3.connect(db_path) as connection:
        connection.executescript("CREATE TABLE plans (id TEXT PRIMARY KEY, scope TEXT, workspace TEXT, goal TEXT, status TEXT, created_at TEXT, updated_at TEXT, metadata_json TEXT); CREATE TABLE plan_steps (id TEXT PRIMARY KEY, plan_id TEXT, position INTEGER, title TEXT, status TEXT, metadata_json TEXT, started_at TEXT, completed_at TEXT, reference TEXT); CREATE TABLE execution_specs (id TEXT PRIMARY KEY, scope TEXT, plan_id TEXT, step_id TEXT, tool_name TEXT, arguments_json TEXT, verification_json TEXT, session_id TEXT, status TEXT, created_at TEXT, updated_at TEXT);")

    class Plan:
        def __init__(self, request):
            self.id = "plan-1"
            self.steps = [type("Step", (), {"id": f"step-{i}"})() for i, _ in enumerate(request.steps)]

    class Plans:
        def create_with_connection(self, connection, request):
            connection.execute("INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("plan-1", request.scope, request.workspace, request.goal, "active", "now", "now", "{}"))
            return Plan(request)

    class Specs:
        def create_with_connection(self, connection, **kwargs):
            spec_id = f"spec-{connection.execute('SELECT COUNT(*) FROM execution_specs').fetchone()[0] + 1}"
            connection.execute("INSERT INTO execution_specs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (spec_id, kwargs["scope"], kwargs["plan_id"], kwargs["step_id"], kwargs["tool_name"], "{}", "{}", None, "draft", "now", "now"))
            return type("Spec", (), {"id": spec_id})()

    workflow_service.plans = Plans()
    workflow_service.specs = Specs()
    proposals.failure_injector = lambda label: (_ for _ in ()).throw(RuntimeError("injected")) if label == "patch_spec_insert_2" else None
    with pytest.raises(RuntimeError, match="injected"):
        proposals.convert(proposal.proposal_id, "s", "ws")
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM execution_specs").fetchone()[0] == 0
    assert proposals.get(proposal.proposal_id, "s").conversion_status is None


def test_preview_is_deterministic_and_reports_stale_state(tmp_path: Path) -> None:
    (tmp_path / "target.txt").write_text("before", encoding="utf-8")
    workflows = _service(tmp_path)
    workflow = workflows.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="edit"))
    workflows._save(workflow.model_copy(update={"status": "awaiting_analysis_review", "analyst_task_id": "analyst"}))
    proposals = CodingProposalService(tmp_path / "state.db", workflows, WorkspaceRegistry({"ws": tmp_path}), workflows.git)
    proposal = proposals.create(workflow.workflow_id, "s", ProposalCreate(changes=[ProposedChange(relative_path="target.txt", expected_text="before", replacement="after", reason="bounded change")]))
    first = proposals.preview(proposal.proposal_id, "s")
    assert first["changes"][0]["stale"] is False
    assert "a/target.txt" in first["changes"][0]["unified_diff"]
    (tmp_path / "target.txt").write_text("changed", encoding="utf-8")
    assert proposals.preview(proposal.proposal_id, "s")["changes"][0]["stale"] is True


def test_rejected_proposal_revision_preserves_history(tmp_path: Path) -> None:
    (tmp_path / "target.txt").write_text("before", encoding="utf-8")
    workflows = _service(tmp_path)
    workflow = workflows.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="edit"))
    workflows._save(workflow.model_copy(update={"status": "awaiting_analysis_review", "analyst_task_id": "analyst"}))
    proposals = CodingProposalService(tmp_path / "state.db", workflows, WorkspaceRegistry({"ws": tmp_path}), workflows.git)
    parent = proposals.create(workflow.workflow_id, "s", ProposalCreate(changes=[ProposedChange(relative_path="target.txt", expected_text="before", replacement="after")]))
    parent = proposals.review(parent.proposal_id, "s", "rejected", "supervisor", "keep the change narrow")
    child = proposals.create_revision(workflow.workflow_id, "s", parent.proposal_id, ProposalCreate(changes=[ProposedChange(relative_path="target.txt", expected_text="before", replacement="after2")]), "use the revised replacement")
    assert child.revision_number == 2 and child.parent_proposal_id == parent.proposal_id
    assert proposals.get(parent.proposal_id, "s").superseded_by_proposal_id == child.proposal_id
    assert [item["revision_number"] for item in proposals.history(workflow.workflow_id, "s")] == [1, 2]
