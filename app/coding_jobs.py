from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.coding_proposals import CodingProposalService
from app.coding_workflows import CodingWorkflowService
from app.plans.specs import ExecutionSpecService
from app.plans.traces import ExecutionTraceService
from app.tools.registry import ToolRegistry


class CodingJobAction(BaseModel):
    action: str
    allowed: bool
    reason: str
    references: list[str] = Field(default_factory=list)


class CodingJobSpec(BaseModel):
    spec_id: str
    tool: str
    status: str
    relative_path: str | None = None
    check_id: str | None = None
    trace_id: str | None = None
    approval_status: str | None = None


class CodingJob(BaseModel):
    job_id: str
    workflow_id: str
    scope: str
    workspace: str
    objective: str
    status: str
    proposal_id: str | None = None
    proposal_revision: int | None = None
    proposal_status: str | None = None
    proposal_preview_available: bool = False
    conversion_status: str | None = None
    patch_specs: list[CodingJobSpec] = Field(default_factory=list)
    check_specs: list[CodingJobSpec] = Field(default_factory=list)
    execution_trace_ids: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    verifier_task_id: str | None = None
    verifier_review_status: str | None = None
    outcome: str | None = None
    next_action: CodingJobAction
    parent_session_id: str | None = None
    parent_plan_id: str | None = None
    parent_step_id: str | None = None
    observed_at: datetime


class CodingJobService:
    def __init__(self, workflows: CodingWorkflowService, proposals: CodingProposalService, specs: ExecutionSpecService, traces: ExecutionTraceService, tools: ToolRegistry) -> None:
        self.workflows, self.proposals, self.specs, self.traces, self.tools = workflows, proposals, specs, traces, tools

    def get(self, workflow_id: str, scope: str) -> CodingJob | None:
        workflow = self.workflows.get(workflow_id, scope)
        if workflow is None:
            return None
        proposal = self._latest_proposal(workflow_id, scope)
        patch_specs = [self._spec_summary(sid, scope, "patch", proposal) for sid in (proposal.patch_spec_ids if proposal else [])]
        check_specs = [self._spec_summary(sid, scope, "check", proposal) for sid in (proposal.check_spec_ids if proposal else [])]
        traces = [*workflow.mutation_trace_ids, *workflow.check_trace_ids]
        status, action = self._derive(workflow, proposal, patch_specs, check_specs)
        changed = sorted({str(self.traces.get(t, scope).result_preview) for t in workflow.mutation_trace_ids if self.traces.get(t, scope) and self.traces.get(t, scope).status == "success" and self.traces.get(t, scope).tool == "filesystem.apply_patch"})
        return CodingJob(job_id=workflow.workflow_id, workflow_id=workflow.workflow_id, scope=scope, workspace=workflow.workspace, objective=workflow.instruction, status=status, proposal_id=proposal.proposal_id if proposal else None, proposal_revision=proposal.revision_number if proposal else None, proposal_status=proposal.status if proposal else None, proposal_preview_available=proposal is not None, conversion_status=proposal.conversion_status if proposal else None, patch_specs=patch_specs, check_specs=check_specs, execution_trace_ids=traces, changed_files=changed, verifier_task_id=workflow.verifier_task_id, verifier_review_status=workflow.verifier_review_status, outcome=workflow.outcome, next_action=action, parent_session_id=workflow.parent_session_id, parent_plan_id=workflow.parent_plan_id, parent_step_id=workflow.parent_step_id, observed_at=datetime.now(UTC))

    def _latest_proposal(self, workflow_id: str, scope: str):
        history = self.proposals.history(workflow_id, scope, 5)
        if not history:
            return None
        return self.proposals.get(history[-1]["proposal_id"], scope)

    def _spec_summary(self, spec_id: str, scope: str, kind: str, proposal) -> CodingJobSpec:
        spec = self.specs.get(spec_id, scope)
        args: dict[str, Any] = spec.arguments if spec else {}
        trace_ids = []
        if proposal:
            trace_ids = proposal.patch_spec_ids if kind == "patch" else proposal.check_spec_ids
        index = trace_ids.index(spec_id) if spec_id in trace_ids else -1
        workflow_trace = []
        if kind == "patch":
            workflow_trace = getattr(self.workflows.get(proposal.workflow_id, scope), "mutation_trace_ids", []) if proposal else []
        else:
            workflow_trace = getattr(self.workflows.get(proposal.workflow_id, scope), "check_trace_ids", []) if proposal else []
        return CodingJobSpec(spec_id=spec_id, tool=spec.tool_name if spec else "", status=spec.status if spec else "unknown", relative_path=args.get("relative_path") if kind == "patch" else None, check_id=args.get("check_id") if kind == "check" else None, trace_id=workflow_trace[index] if index >= 0 and index < len(workflow_trace) else None)

    def _derive(self, workflow, proposal, patches, checks):
        if workflow.status in {"completed", "failed", "cancelled"}:
            return workflow.status, CodingJobAction(action="none", allowed=False, reason="workflow terminal")
        if workflow.status == "awaiting_analysis":
            return workflow.status, CodingJobAction(action="start_analysis", allowed=True, reason="analysis has not started")
        if workflow.status == "awaiting_analysis_review":
            return workflow.status, CodingJobAction(action="review_analysis", allowed=True, reason="analyst review required", references=[workflow.analyst_task_id] if workflow.analyst_task_id else [])
        if proposal is None:
            return "awaiting_proposal", CodingJobAction(action="create_proposal", allowed=True, reason="accepted analysis has no proposal")
        if proposal.status == "rejected":
            return "proposal_rejected", CodingJobAction(action="request_revision", allowed=True, reason="proposal rejected")
        if proposal.status != "accepted":
            return "awaiting_proposal_review", CodingJobAction(action="review_proposal", allowed=True, reason="proposal review required", references=[proposal.proposal_id])
        if proposal.conversion_status != "converted":
            return "awaiting_spec_conversion", CodingJobAction(action="convert_proposal", allowed=True, reason="accepted proposal has no execution specs")
        if proposal.spec_review_status != "accepted":
            return "awaiting_spec_review", CodingJobAction(action="review_specs", allowed=True, reason="generated specs require review")
        if any(item.status in {"draft", "invalidated"} for item in patches):
            return "awaiting_patch_approval", CodingJobAction(action="request_patch_approval", allowed=True, reason="patch specs are not ready")
        if workflow.status in {"implementation_ready", "implementing"}:
            return "ready_to_execute_patches", CodingJobAction(action="execute_patches", allowed=True, reason="approved patch execution is available")
        if workflow.status == "awaiting_checks":
            return workflow.status, CodingJobAction(action="execute_checks", allowed=True, reason="checks are ready")
        if workflow.status == "awaiting_verification":
            return "awaiting_verifier", CodingJobAction(action="start_verifier", allowed=True, reason="checks succeeded")
        if workflow.status == "awaiting_verifier_review":
            return workflow.status, CodingJobAction(action="review_verifier", allowed=True, reason="verifier review required")
        return workflow.status, CodingJobAction(action="none", allowed=False, reason="no safe action available")
