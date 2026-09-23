from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.coding_actions import CodingJobActionRequest
from app.coding_guidance import CodingGuidanceService
from app.coding_jobs import CodingJobService
from app.coding_proposals import CodingProposalService
from app.coding_workflows import CodingWorkflowCreate
from app.plans.specs import ExecutionSpecService
from app.policy.workspaces import WorkspaceRegistry
from app.tools.registry import ToolRegistry
from tests.test_coding_workflows import _service


def test_guidance_is_derived_and_read_only(tmp_path: Path) -> None:
    workflows = _service(tmp_path)
    workflow = workflows.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="inspect"))
    proposals = CodingProposalService(tmp_path / "state.db", workflows, WorkspaceRegistry({"ws": tmp_path}), workflows.git)
    service = CodingGuidanceService(CodingJobService(workflows, proposals, ExecutionSpecService(tmp_path / "state.db", workflows.plans, ToolRegistry()), object(), ToolRegistry()))
    first = service.get(workflow.workflow_id, "s")
    second = service.get(workflow.workflow_id, "s")
    assert first is not None
    assert first.next_action == "start_analysis"
    assert first.confirmation_required is True
    assert first.will_do == ["dispatch one code analyst task"]
    assert second is not None
    assert second.next_action == first.next_action


def test_guidance_api_is_scoped(monkeypatch, tmp_path: Path) -> None:
    workflows = _service(tmp_path)
    workflow = workflows.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="inspect"))
    proposals = CodingProposalService(tmp_path / "state.db", workflows, WorkspaceRegistry({"ws": tmp_path}), workflows.git)
    monkeypatch.setattr(main, "coding_guidance_service", CodingGuidanceService(CodingJobService(workflows, proposals, ExecutionSpecService(tmp_path / "state.db", workflows.plans, ToolRegistry()), object(), ToolRegistry())))
    client = TestClient(main.app)
    assert client.get(f"/supervisor-coding-workflows/{workflow.workflow_id}/guidance", params={"scope": "s"}).status_code == 200
    assert client.get(f"/supervisor-coding-workflows/{workflow.workflow_id}/guidance", params={"scope": "other"}).status_code == 404


def test_guidance_action_registry_matches_supervisor_action_registry() -> None:
    actions = set(CodingJobActionRequest.model_json_schema()["properties"]["action"]["enum"])
    supported = {
        "start_analysis", "review_analysis", "create_proposal", "review_proposal",
        "request_revision", "convert_proposal", "review_specs", "request_patch_approval",
        "review_patch_approvals", "execute_patches", "execute_checks", "start_verifier",
        "review_verifier",
    }
    assert actions == supported
