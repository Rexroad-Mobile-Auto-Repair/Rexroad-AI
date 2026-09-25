from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.supervisor_workflows import (
    ResearchVerifyWorkflow,
    ResearchVerifyWorkflowResult,
    SupervisorResearchVerifyWorkflow,
)


class ResearchWorkflowActionRequest(BaseModel):
    action: Literal["accept_research", "reject_research", "start_verification", "accept_verifier", "reject_verifier"]
    reviewer_session_id: str | None = None
    note: str | None = Field(default=None, max_length=2000)


class ResearchWorkflowActionResult(BaseModel):
    workflow_id: str
    scope: str
    status: str
    next_action: str
    blocked_reason: str | None = None
    plan_id: str | None = None
    step_id: str | None = None
    workflow: ResearchVerifyWorkflow


class ResearchWorkflowActionService:
    def __init__(self, workflows: SupervisorResearchVerifyWorkflow) -> None:
        self.workflows = workflows

    async def dispatch(self, workflow_id: str, scope: str, request: ResearchWorkflowActionRequest) -> ResearchWorkflowActionResult:
        if self.workflows.get(workflow_id, scope) is None:
            raise ValueError("research workflow not found")
        if request.action == "accept_research":
            result = self.workflows.review_research(workflow_id, scope, "accepted", request.reviewer_session_id, request.note)
        elif request.action == "reject_research":
            result = self.workflows.review_research(workflow_id, scope, "rejected", request.reviewer_session_id, request.note)
        elif request.action == "start_verification":
            result = await self.workflows.start_verification(workflow_id, scope)
        elif request.action == "accept_verifier":
            result = self.workflows.review_verifier(workflow_id, scope, "accepted", request.reviewer_session_id, request.note)
        else:
            result = self.workflows.review_verifier(workflow_id, scope, "rejected", request.reviewer_session_id, request.note)
        return self._result(result)

    @staticmethod
    def _result(result: ResearchVerifyWorkflowResult) -> ResearchWorkflowActionResult:
        workflow = result.workflow
        if workflow.status == "awaiting_review" and workflow.researcher_review_status != "accepted":
            next_action = "accept_research"
        elif workflow.status == "awaiting_review":
            next_action = "start_verification"
        elif workflow.status == "awaiting_verifier_review":
            next_action = "accept_verifier"
        else:
            next_action = "none" if workflow.status in {"completed", "failed", "cancelled"} else "start_research"
        return ResearchWorkflowActionResult(workflow_id=workflow.workflow_id, scope=workflow.scope, status=workflow.status, next_action=next_action, blocked_reason=workflow.final_outcome if next_action == "none" else None, plan_id=workflow.plan_id, step_id=workflow.step_id, workflow=workflow)
