from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.coding_jobs import CodingJob, CodingJobService
from app.coding_proposals import CodingProposalService, ProposalCreate
from app.coding_workflows import CheckAction, CodingWorkflowService, PatchAction
from app.tools.registry import ToolRegistry


class CodingJobActionRequest(BaseModel):
    action: Literal[
        "start_analysis", "review_analysis", "create_proposal", "review_proposal",
        "request_revision", "convert_proposal", "review_specs", "request_patch_approval",
        "review_patch_approvals", "execute_patches", "prepare_checks", "execute_checks",
        "start_verifier", "review_verifier"
    ]
    scope: str
    decision: Literal["accept", "reject"] | None = None
    note: str | None = Field(default=None, max_length=2000)
    reviewer_session_id: str | None = None
    proposal: ProposalCreate | None = None
    patches: list[PatchAction] | None = Field(default=None, max_length=5)
    checks: list[CheckAction] | None = Field(default=None, max_length=5)
    approval_request_id: str | None = None


class CodingJobActionResult(BaseModel):
    action: str
    workflow_id: str
    status: str
    job_before_status: str
    reason: str
    affected_ids: list[str] = Field(default_factory=list)
    job: CodingJob


class SupervisorCodingActionService:
    def __init__(self, jobs: CodingJobService, workflows: CodingWorkflowService, proposals: CodingProposalService, tools: ToolRegistry) -> None:
        self.jobs, self.workflows, self.proposals, self.tools = jobs, workflows, proposals, tools

    async def dispatch(self, workflow_id: str, request: CodingJobActionRequest) -> CodingJobActionResult:
        before = self.jobs.get(workflow_id, request.scope)
        if before is None:
            raise ValueError("coding job not found")
        if before.next_action.action != request.action or not before.next_action.allowed:
            raise ValueError("action is not currently permitted")
        affected: list[str] = []
        proposal = self.proposals.get(before.proposal_id, request.scope) if before.proposal_id else None
        action = request.action
        if action == "start_analysis":
            result = await self.workflows.start_analysis(workflow_id, request.scope)
            affected = [item for item in (result.analyst_task_id, result.analyst_dispatch_id) if item]
        elif action == "review_analysis":
            if request.decision is None or not before.analyst_task_id: raise ValueError("review decision required")
            reviewed = self.workflows.review_analysis(workflow_id, request.scope, "accepted" if request.decision == "accept" else "rejected", request.reviewer_session_id, request.note)
            affected = [before.analyst_task_id, reviewed.analyst_review_status or ""]
        elif action == "create_proposal":
            if request.proposal is None: raise ValueError("proposal required")
            created = self.proposals.create(workflow_id, request.scope, request.proposal)
            affected = [created.proposal_id]
        elif action == "review_proposal":
            if proposal is None or request.decision is None: raise ValueError("proposal review decision required")
            updated = self.proposals.review(proposal.proposal_id, request.scope, "accepted" if request.decision == "accept" else "rejected", request.reviewer_session_id, request.note)
            affected = [updated.proposal_id]
        elif action == "request_revision":
            if proposal is None or not request.note: raise ValueError("revision note required")
            result = await self.proposals.request_revision(proposal.proposal_id, request.scope, request.note)
            affected = [str(result.get("task_id"))]
        elif action == "convert_proposal":
            if proposal is None: raise ValueError("proposal unavailable")
            updated = self.proposals.convert(proposal.proposal_id, request.scope, before.workspace)
            first_spec = self.workflows.specs.get((updated.patch_spec_ids or updated.check_spec_ids)[0], request.scope) if (updated.patch_spec_ids or updated.check_spec_ids) else None
            if first_spec is None: raise ValueError("converted specs unavailable")
            self.workflows.bind_converted_specs(workflow_id, request.scope, first_spec.plan_id, updated.patch_spec_ids, updated.check_spec_ids)
            affected = [updated.proposal_id, *updated.patch_spec_ids, *updated.check_spec_ids]
        elif action == "review_specs":
            if proposal is None or request.decision is None: raise ValueError("spec review decision required")
            updated = self.proposals.accept_specs(proposal.proposal_id, request.scope, before.workspace, request.reviewer_session_id) if request.decision == "accept" else self.proposals.reject_specs(proposal.proposal_id, request.scope, request.reviewer_session_id)
            affected = [updated.proposal_id, *updated.patch_spec_ids, *updated.check_spec_ids]
        elif action == "request_patch_approval":
            if proposal is None: raise ValueError("proposal unavailable")
            for spec_id in proposal.patch_spec_ids:
                spec = self.workflows.specs.get(spec_id, request.scope)
                if spec is None or spec.status != "ready": raise ValueError("patch spec is not ready")
                existing = self.tools.find_approval_request(tool=spec.tool_name, scope=request.scope, session_id=spec.session_id, arguments=spec.arguments)
                if existing is None:
                    authorization = self.tools.authorize(spec.tool_name, request.scope, spec.session_id)
                    existing = self.tools.request_approval(authorization, spec.arguments, f"Approve patch {spec.arguments.get('relative_path', '')}")
                affected.append(existing.id)
        elif action == "review_patch_approvals":
            if not request.approval_request_id or request.decision is None: raise ValueError("approval decision required")
            reviewed = self.tools.review_approval(request.approval_request_id, request.scope, request.decision == "accept")
            affected = [reviewed.id]
        elif action == "execute_patches":
            if proposal is None: raise ValueError("proposal unavailable")
            ids = [item.approval_request_id for item in before.patch_specs if item.approval_request_id]
            if len(ids) != len(proposal.patch_spec_ids): raise ValueError("approval required for every patch")
            updated = self.workflows.execute_implementation(workflow_id, request.scope, ids)
            affected = [*updated.mutation_trace_ids]
        elif action == "prepare_checks":
            if request.checks is None: raise ValueError("checks required")
            updated = self.workflows.prepare_checks(workflow_id, request.scope, request.checks)
            affected = [*updated.check_spec_ids]
        elif action == "execute_checks":
            updated = self.workflows.execute_checks(workflow_id, request.scope)
            affected = [*updated.check_trace_ids]
        elif action == "start_verifier":
            updated = await self.workflows.start_verification(workflow_id, request.scope)
            affected = [item for item in (updated.verifier_task_id, updated.verifier_dispatch_id) if item]
        elif action == "review_verifier":
            if request.decision is None: raise ValueError("verifier review decision required")
            updated = self.workflows.review_verifier(workflow_id, request.scope, "accepted" if request.decision == "accept" else "rejected", request.reviewer_session_id, request.note)
            affected = [item for item in (updated.verifier_task_id,) if item]
        after = self.jobs.get(workflow_id, request.scope)
        if after is None: raise ValueError("coding job unavailable")
        return CodingJobActionResult(action=action, workflow_id=workflow_id, status=after.status, job_before_status=before.status, reason="one explicit action completed", affected_ids=affected, job=after)
