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
