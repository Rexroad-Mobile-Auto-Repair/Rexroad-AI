from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.coding_jobs import CodingJobService
from app.coding_proposals import CodingProposalService
from app.coding_workflows import CodingWorkflowCreate
from app.plans.specs import ExecutionSpecService
from app.plans.traces import ExecutionTraceService
from app.policy.workspaces import WorkspaceRegistry
from app.tools.registry import ToolRegistry
from tests.test_coding_workflows import _service


def _job_service(tmp_path: Path):
    workflows = _service(tmp_path)
    workflow = workflows.create(CodingWorkflowCreate(scope="scope", workspace="ws", instruction="inspect"))
    proposals = CodingProposalService(tmp_path / "state.db", workflows, WorkspaceRegistry({"ws": tmp_path}), workflows.git)
    service = CodingJobService(workflows, proposals, ExecutionSpecService(tmp_path / "state.db", workflows.plans, ToolRegistry()), ExecutionTraceService(main.action_journal), ToolRegistry())
    return service, workflow


def test_job_is_derived_and_reports_start_analysis(tmp_path: Path) -> None:
    service, workflow = _job_service(tmp_path)
    job = service.get(workflow.workflow_id, "scope")
    assert job is not None
    assert job.job_id == workflow.workflow_id
    assert job.status == "awaiting_analysis"
    assert job.next_action.action == "start_analysis"


def test_job_api_is_scoped_and_read_only(monkeypatch, tmp_path: Path) -> None:
    service, workflow = _job_service(tmp_path)
    monkeypatch.setattr(main, "coding_job_service", service)
    client = TestClient(main.app)
    response = client.get(f"/supervisor-coding-workflows/{workflow.workflow_id}/job", params={"scope": "scope"})
    assert response.status_code == 200
    assert response.json()["next_action"]["action"] == "start_analysis"
    assert client.get(f"/supervisor-coding-workflows/{workflow.workflow_id}/job", params={"scope": "other"}).status_code == 404
