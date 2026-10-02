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
    approval_request_id: str | None = None
    execution_status: str = "not_attempted"
    changed: bool | None = None
    result_summary: dict[str, Any] | None = None
    passed: bool | None = None
    exit_code: int | None = None
    timed_out: bool | None = None
    failure_reason: str | None = None
    attempt_count: int = 0
    attempts: list[dict[str, Any]] = Field(default_factory=list)


class CodingJob(BaseModel):
    job_id: str
    workflow_id: str
    scope: str
    workspace: str
    objective: str
    status: str
    proposal_id: str | None = None
    analyst_task_id: str | None = None
    proposal_revision: int | None = None
    proposal_status: str | None = None
    proposal_preview_available: bool = False
    conversion_status: str | None = None
    patch_specs: list[CodingJobSpec] = Field(default_factory=list)
    check_specs: list[CodingJobSpec] = Field(default_factory=list)
    execution_trace_ids: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None
    verifier_task_id: str | None = None
    verifier_review_status: str | None = None
    verification_conflicts: list[str] = Field(default_factory=list)
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
        check_specs = [self._spec_summary(sid, scope, "check", proposal) for sid in (workflow.check_spec_ids or (proposal.check_spec_ids if proposal else []))]
        traces = [*workflow.mutation_trace_ids, *workflow.check_trace_ids]
        status, action = self._derive(workflow, proposal, patch_specs, check_specs)
        changed = sorted({item.relative_path for item in patch_specs if item.changed is True and item.relative_path})
        blocked = self._blocked_reason(workflow, proposal, patch_specs, check_specs)
        return CodingJob(job_id=workflow.workflow_id, workflow_id=workflow.workflow_id, scope=scope, workspace=workflow.workspace, objective=workflow.instruction, status=status, proposal_id=proposal.proposal_id if proposal else None, analyst_task_id=workflow.analyst_task_id, proposal_revision=proposal.revision_number if proposal else None, proposal_status=proposal.status if proposal else None, proposal_preview_available=proposal is not None, conversion_status=proposal.conversion_status if proposal else None, patch_specs=patch_specs, check_specs=check_specs, execution_trace_ids=traces, changed_files=changed, blocked_reason=blocked, verifier_task_id=workflow.verifier_task_id, verifier_review_status=workflow.verifier_review_status, verification_conflicts=self.workflows.verification_conflicts(workflow_id, scope), outcome=workflow.outcome, next_action=action, parent_session_id=workflow.parent_session_id, parent_plan_id=workflow.parent_plan_id, parent_step_id=workflow.parent_step_id, observed_at=datetime.now(UTC))

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
        workflow_item = self.workflows.get(proposal.workflow_id, scope) if proposal else None
        explicit_attempts = [attempt for attempt in getattr(workflow_item, "execution_attempts", []) if attempt.spec_id == spec_id and attempt.kind == kind]
        if not explicit_attempts:
            if kind == "patch":
                workflow_trace = getattr(workflow_item, "mutation_trace_ids", []) if workflow_item else []
            else:
                workflow_trace = getattr(workflow_item, "check_trace_ids", []) if workflow_item else []
        trace_id = workflow_trace[index] if index >= 0 and index < len(workflow_trace) else None
        if explicit_attempts:
            explicit_attempts = sorted(explicit_attempts, key=lambda attempt: (attempt.sequence, attempt.created_at, attempt.attempt_id))[:10]
            latest = explicit_attempts[-1]
            trace_id = latest.trace_id
        trace = self.traces.get(trace_id, scope) if trace_id else None
        execution_status = "succeeded" if trace and trace.status == "success" else ("failed" if trace else "not_attempted")
        summary: dict[str, Any] | None = None
        changed: bool | None = None
        passed: bool | None = None
        exit_code: int | None = None
        timed_out: bool | None = None
        failure_reason = trace.verification_reason if trace and trace.status != "success" else None
        if trace and trace.result_preview:
            import ast
            try:
                parsed = ast.literal_eval(trace.result_preview)
                if isinstance(parsed, dict):
                    summary = {key: parsed[key] for key in ("relative_path", "before_hash", "after_hash", "bytes_changed", "check_id", "status", "targets") if key in parsed}
                    changed = parsed.get("changed") if kind == "patch" else None
                    passed = parsed.get("passed") if kind == "check" else None
                    exit_code = parsed.get("exit_code") if kind == "check" else None
                    timed_out = parsed.get("timed_out") if kind == "check" else None
            except (ValueError, SyntaxError):
                pass
        if kind == "patch" and explicit_attempts:
            for attempt in explicit_attempts:
                if attempt.status != "succeeded" or not attempt.trace_id:
                    continue
                historical = self.traces.get(attempt.trace_id, scope)
                if historical and historical.result_preview:
                    import ast
                    try:
                        parsed_historical = ast.literal_eval(historical.result_preview)
                        if isinstance(parsed_historical, dict) and parsed_historical.get("changed") is True:
                            changed = True
                            break
                    except (ValueError, SyntaxError):
                        continue
        approval_status = None
        approval_request_id = None
        if spec and kind == "patch":
            request = self.tools.find_approval_request(tool=spec.tool_name, scope=scope, session_id=spec.session_id, arguments=spec.arguments)
            approval_status = request.status if request else "not_requested"
            approval_request_id = request.id if request else None
        attempt_summaries = [{"sequence": attempt.sequence, "status": attempt.status, "trace_id": attempt.trace_id, "created_at": attempt.created_at} for attempt in explicit_attempts]
        if explicit_attempts:
            latest_status = explicit_attempts[-1].status
            execution_status = "succeeded" if latest_status == "succeeded" else ("failed" if latest_status == "failed" else "not_attempted")
        return CodingJobSpec(spec_id=spec_id, tool=spec.tool_name if spec else "", status=spec.status if spec else "unknown", relative_path=args.get("relative_path") if kind == "patch" else None, check_id=args.get("check_id") if kind == "check" else None, trace_id=trace_id, approval_status=approval_status, approval_request_id=approval_request_id, execution_status=execution_status, changed=changed, result_summary=summary, passed=passed, exit_code=exit_code, timed_out=timed_out, failure_reason=failure_reason, attempt_count=len(explicit_attempts), attempts=attempt_summaries)

    def _blocked_reason(self, workflow, proposal, patches, checks) -> str | None:
        if workflow.status in {"completed", "failed", "cancelled"}:
            return "workflow terminal"
        if workflow.status == "awaiting_analysis_review":
            return "analysis review pending"
        if proposal and proposal.status == "rejected":
            return "proposal rejected"
        if proposal and proposal.spec_review_status != "accepted":
            return "spec review pending"
        if any(item.approval_status == "rejected" for item in patches):
            return "approval rejected"
        if any(item.approval_status in {"not_requested", "pending"} for item in patches):
            return "approval pending"
        if any(item.execution_status == "failed" for item in patches + checks):
            return "execution failed"
        if any(item.execution_status == "failed" or item.passed is False for item in checks):
            return "check failed"
        if workflow.status == "awaiting_verifier_review":
            return "verifier review pending"
        return None

    def _derive(self, workflow, proposal, patches, checks):
        if workflow.status == "failed" and workflow.outcome == "rejected" and workflow.verifier_review_status == "rejected" and workflow.check_trace_ids:
            return "verification_rejected", CodingJobAction(action="retry_verifier", allowed=True, reason="retry read-only verification using saved check evidence")
        if workflow.status == "failed" and workflow.outcome == "check_failed" and workflow.mutation_trace_ids:
            return "checks_failed", CodingJobAction(action="retry_checks", allowed=True, reason="retry saved checks without reapplying files")
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
        if any(item.approval_status == "rejected" for item in patches):
            return "awaiting_patch_approval", CodingJobAction(action="request_patch_approval", allowed=False, reason="a patch approval was rejected")
        if any(item.approval_status in {"not_requested", "pending"} for item in patches):
            pending = any(item.approval_status == "pending" for item in patches)
            if pending:
                return "awaiting_patch_approval", CodingJobAction(action="review_patch_approvals", allowed=True, reason="patch approval is pending")
            return "awaiting_patch_approval", CodingJobAction(action="request_patch_approval", allowed=True, reason="patch approval is required")
        if workflow.status in {"implementation_ready", "implementing"}:
            return "ready_to_execute_patches", CodingJobAction(action="execute_patches", allowed=True, reason="approved patch execution is available")
        if workflow.status == "awaiting_checks":
            if any(item.execution_status == "failed" or item.passed is False for item in checks):
                return "checks_failed", CodingJobAction(action="none", allowed=False, reason="a check failed")
            if checks and all(item.execution_status == "succeeded" and item.passed is True for item in checks):
                return "awaiting_verification", CodingJobAction(action="start_verifier", allowed=True, reason="checks succeeded")
            return workflow.status, CodingJobAction(action="execute_checks", allowed=True, reason="checks are ready")
        if workflow.status == "awaiting_verification":
            return "awaiting_verifier", CodingJobAction(action="start_verifier", allowed=True, reason="checks succeeded")
        if workflow.status == "awaiting_verifier_review":
            return workflow.status, CodingJobAction(action="review_verifier", allowed=True, reason="verifier review required")
        return workflow.status, CodingJobAction(action="none", allowed=False, reason="no safe action available")
