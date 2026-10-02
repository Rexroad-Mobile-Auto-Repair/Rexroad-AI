from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.coding_actions import CodingJobActionRequest
from app.coding_guidance import CodingGuidanceService
from app.coding_jobs import CodingJob, CodingJobAction, CodingJobService, CodingJobSpec
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
        "review_verifier", "retry_checks", "retry_verifier",
    }
    assert actions == supported


def _job(action: str, *, status: str = "awaiting_patch_approval", high_impact: bool = False) -> CodingJob:
    return CodingJob(
        job_id="wf", workflow_id="wf", scope="s", workspace="ws", objective="edit",
        status=status,
        patch_specs=[
            CodingJobSpec(spec_id="p1", tool="filesystem.apply_patch", status="ready", relative_path="app/example.py", approval_status="pending", approval_request_id="r1"),
            CodingJobSpec(spec_id="p2", tool="filesystem.apply_patch", status="ready", relative_path="app/helpers.py", approval_status="approved", approval_request_id="r2"),
        ],
        next_action=CodingJobAction(action=action, allowed=True, reason="explicit action"),
        observed_at="2025-01-01T00:00:00Z",
    )


def test_guidance_multi_patch_approval_is_bounded_and_does_not_leak_secrets() -> None:
    class Jobs:
        def get(self, workflow_id: str, scope: str) -> CodingJob:
            return _job("review_patch_approvals")

    guidance = CodingGuidanceService(Jobs()).get("wf", "s")
    assert guidance is not None
    assert guidance.next_action == "review_patch_approvals"
    assert guidance.action_preview == {"pending_request_ids": ["r1"], "approved_count": 1}
    assert guidance.affected_resources == ["app/example.py", "app/helpers.py"]
    assert guidance.safe_parameters == {"approval_request_id": "<required>", "decision": "<required: accept|reject>"}
    serialized = guidance.model_dump_json()
    assert "expected_text" not in serialized
    assert "replacement" not in serialized
    assert "token" not in serialized.lower()


def test_guidance_execute_patches_is_the_only_high_impact_action() -> None:
    class Jobs:
        def get(self, workflow_id: str, scope: str) -> CodingJob:
            return _job("execute_patches", status="ready_to_execute_patches")

    service = CodingGuidanceService(Jobs())
    assert service.get("wf", "s").high_impact is True
    for action in ("review_proposal", "review_specs", "request_patch_approval", "review_patch_approvals", "execute_checks", "start_verifier", "review_verifier"):
        class CurrentJobs:
            def get(self, workflow_id: str, scope: str, action=action) -> CodingJob:
                return _job(action)
        assert CodingGuidanceService(CurrentJobs()).get("wf", "s").high_impact is False


def test_guidance_terminal_failure_and_cancellation_never_offer_retry() -> None:
    for status in ("failed", "cancelled", "completed"):
        class Jobs:
            def get(self, workflow_id: str, scope: str, status=status) -> CodingJob:
                item = _job("none", status=status)
                return item.model_copy(
                    update={
                        "next_action": CodingJobAction(
                            action="none", allowed=False, reason="workflow terminal"
                        )
                    }
                )

        guidance = CodingGuidanceService(Jobs()).get("wf", "s")
        assert guidance is not None
        assert guidance.next_action == "none"
        assert guidance.action_available is False
        assert guidance.confirmation_required is False
        assert guidance.high_impact is False
        assert "retry" not in guidance.explanation.casefold()


def test_completed_verified_guidance_explains_success_without_actions():
    class Jobs:
        def get(self, workflow_id, scope):
            return _job("none", status="completed").model_copy(update={"outcome": "verified", "next_action": CodingJobAction(action="none", allowed=False, reason="workflow terminal")})
    guidance = CodingGuidanceService(Jobs()).get("wf", "s")
    assert guidance.headline == "Work verified."
    assert guidance.explanation == "The file changes and checks completed, and the verifier report was accepted."
    assert guidance.action_available is False and guidance.confirmation_required is False


def test_revision_guidance_requires_explicit_note_without_materialization() -> None:
    class Jobs:
        def get(self, workflow_id: str, scope: str) -> CodingJob:
            return CodingJob(
                job_id="wf", workflow_id="wf", scope="s", workspace="ws", objective="edit",
                status="proposal_rejected", proposal_id="proposal-1", proposal_revision=1,
                proposal_status="rejected", next_action=CodingJobAction(action="request_revision", allowed=True, reason="proposal rejected"), observed_at="2025-01-01T00:00:00Z",
            )
    guidance = CodingGuidanceService(Jobs()).get("wf", "s")
    assert guidance is not None
    assert guidance.next_action == "request_revision"
    assert guidance.safe_parameters == {"note": "<required bounded revision note>"}
    assert guidance.will_do == ["dispatch one revision analyst task"]
    assert any("materialize" in value for value in guidance.will_not_do)
    assert any("accept" in value for value in guidance.will_not_do)
