from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.coding_jobs import CodingJob, CodingJobService


class CodingJobGuidance(BaseModel):
    workflow_id: str
    scope: str
    workspace: str
    job_status: str
    next_action: str
    action_available: bool
    headline: str
    explanation: str
    why_now: str
    affected_resources: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    action_preview: dict[str, Any] = Field(default_factory=dict)
    will_do: list[str] = Field(default_factory=list)
    will_not_do: list[str] = Field(default_factory=list)
    confirmation_required: bool
    blocking_reason: str | None = None
    safe_parameters: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime


class CodingGuidanceService:
    MAX_ITEMS = 20

    def __init__(self, jobs: CodingJobService) -> None:
        self.jobs = jobs

    def get(self, workflow_id: str, scope: str) -> CodingJobGuidance | None:
        job = self.jobs.get(workflow_id, scope)
        if job is None:
            return None
        action = job.next_action.action
        resources = self._resources(job)
        terminal = action == "none"
        confirmation = not terminal
        headline = "No further action is available." if terminal else f"Next explicit action: {action}."
        explanation = job.next_action.reason[:500]
        preview: dict[str, Any] = {}
        safe: dict[str, Any] = {}
        will_do: list[str] = []
        will_not = ["continue automatically", "commit or push Git", "write memory or Knowledge"]
        if action == "start_analysis":
            explanation = "Dispatch the read-only code analyst for the current objective."
            will_do = ["dispatch one code analyst task"]
            will_not += ["review the analyst result", "create a proposal"]
        elif action == "review_analysis":
            safe = {"decision": "<required: accept|reject>"}; will_do = ["record the explicit analyst review decision"]; will_not += ["create a proposal", "execute tools"]
        elif action == "create_proposal":
            safe = {"proposal": "<required ProposalCreate input>"}; will_do = ["create one proposal in review state"]; will_not += ["accept or convert the proposal"]
        elif action == "review_proposal":
            safe = {"decision": "<required: accept|reject>", "note": "<optional bounded note>"}; will_do = ["record the proposal review decision"]; will_not += ["convert or execute the proposal"]
        elif action == "request_revision":
            safe = {"note": "<required bounded revision note>"}; will_do = ["dispatch one revision analyst task"]; will_not += ["materialize or accept a revised proposal"]
        elif action == "convert_proposal":
            preview = {"proposal_id": job.proposal_id, "revision": job.proposal_revision}; safe = {}; will_do = ["create the persisted plan and execution specs"]; will_not += ["review specs", "request approvals"]
        elif action == "review_specs":
            preview = {"patch_count": len(job.patch_specs), "check_count": len(job.check_specs)}; safe = {"decision": "<required: accept|reject>"}; will_do = ["accept or reject generated specs"]; will_not += ["create approvals", "execute patches"]
        elif action == "request_patch_approval":
            preview = {"patch_count": len(job.patch_specs), "files": resources}; safe = {}; will_do = ["create pending approval requests for ready patch specs"]; will_not += ["approve requests", "execute patches"]
        elif action == "review_patch_approvals":
            pending = [item.approval_request_id for item in job.patch_specs if item.approval_status == "pending"]
            preview = {"pending_request_ids": pending[: self.MAX_ITEMS], "approved_count": sum(item.approval_status == "approved" for item in job.patch_specs)}
            safe = {"approval_request_id": "<required>", "decision": "<required: accept|reject>"}; will_do = ["record one approval decision"]; will_not += ["execute patches", "mint a capability"]
        elif action == "execute_patches":
            preview = {"patch_count": len(job.patch_specs), "files": resources}; safe = {}; will_do = [f"execute {len(job.patch_specs)} approved filesystem patches", "record attempts and traces"]; will_not += ["run checks", "dispatch verifier"]
        elif action == "execute_checks":
            preview = {"checks": [item.check_id for item in job.check_specs[: self.MAX_ITEMS]], "targets": [item.result_summary.get("targets", []) if item.result_summary else [] for item in job.check_specs[: self.MAX_ITEMS]]}; safe = {}; will_do = ["run only registered workspace checks"]; will_not += ["run arbitrary commands", "dispatch verifier"]
        elif action == "start_verifier":
            safe = {}; will_do = ["dispatch one read-only verifier task"]; will_not += ["accept the verifier result"]
        elif action == "review_verifier":
            preview = {"verifier_task_id": job.verifier_task_id, "review_status": job.verifier_review_status}; safe = {"decision": "<required: accept|reject>"}; will_do = ["record the explicit verifier review"]; will_not += ["run another worker"]
        else:
            will_do = []
        blocked = job.blocked_reason
        return CodingJobGuidance(workflow_id=job.workflow_id, scope=job.scope, workspace=job.workspace, job_status=job.status, next_action=action, action_available=job.next_action.allowed, headline=headline, explanation=explanation, why_now=job.next_action.reason[:500], affected_resources=resources[: self.MAX_ITEMS], prerequisites=job.next_action.references[: self.MAX_ITEMS], action_preview=preview, will_do=will_do[:10], will_not_do=will_not[:10], confirmation_required=confirmation, blocking_reason=blocked, safe_parameters=safe, observed_at=datetime.now(UTC))

    @staticmethod
    def _resources(job: CodingJob) -> list[str]:
        return sorted({item.relative_path for item in job.patch_specs if item.relative_path})[:20]
