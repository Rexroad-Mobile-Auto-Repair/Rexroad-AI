from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from app.coding_guidance import CodingGuidanceService
from app.coding_jobs import CodingJobService
from app.coding_workflows import CodingWorkflowService
from app.plans.service import PlanService
from app.plans.traces import ExecutionTraceService
from app.policy.workspaces import WorkspaceRegistry
from app.project_state import ProjectStateService
from app.supervisor_workflows import SupervisorResearchVerifyWorkflow
from app.tools.registry import ToolRegistry

AttentionCategory = Literal["blocked_failure", "approval_required", "review_required", "user_input_required", "action_ready", "informational"]


class DashboardCodingItem(BaseModel):
    workflow_id: str
    status: str
    objective: str
    next_action: str
    action_available: bool
    guidance_headline: str
    blocking_reason: str | None = None
    high_impact: bool = False
    affected_resources: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class DashboardResearchItem(BaseModel):
    workflow_id: str
    status: str
    researcher_review_status: str | None = None
    verifier_review_status: str | None = None
    updated_at: datetime | None = None


class DashboardPlanItem(BaseModel):
    plan_id: str
    status: str
    step_count: int
    completed_step_count: int
    failed_step_count: int
    updated_at: datetime | None = None


class DashboardActivity(BaseModel):
    id: str
    category: str
    status: str
    timestamp: datetime
    summary: str
    trace_id: str | None = None


class SupervisorAttentionItem(BaseModel):
    id: str
    category: AttentionCategory
    source_type: str
    source_id: str
    workspace: str
    headline: str
    reason: str
    next_action: str | None = None
    high_impact: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    references: list[str] = Field(default_factory=list)


class DashboardSummary(BaseModel):
    active_coding_jobs: int
    active_research_workflows: int
    active_plans: int
    blocked_items: int
    pending_approvals: int
    pending_reviews: int
    ready_actions: int


class SupervisorProjectDashboard(BaseModel):
    scope: str
    workspace: str
    generated_at: datetime
    project: dict[str, Any]
    coding: list[DashboardCodingItem]
    research: list[DashboardResearchItem]
    plans: list[DashboardPlanItem]
    recent_activity: list[DashboardActivity]
    attention: list[SupervisorAttentionItem]
    history: list[SupervisorAttentionItem] = Field(default_factory=list)
    summary: DashboardSummary


class SupervisorDashboardService:
    CATEGORY_ORDER: ClassVar[dict[str, int]] = {"blocked_failure": 0, "approval_required": 1, "review_required": 2, "user_input_required": 3, "action_ready": 4, "informational": 5}

    def __init__(self, workspaces: WorkspaceRegistry, state: ProjectStateService, coding_workflows: CodingWorkflowService, jobs: CodingJobService, guidance: CodingGuidanceService, research: SupervisorResearchVerifyWorkflow, plans: PlanService, traces: ExecutionTraceService, tools: ToolRegistry) -> None:
        self.workspaces, self.state, self.coding_workflows, self.jobs, self.guidance, self.research, self.plans, self.traces, self.tools = workspaces, state, coding_workflows, jobs, guidance, research, plans, traces, tools

    def get(self, scope: str, workspace: str, *, coding_limit: int = 20, research_limit: int = 20, plan_limit: int = 20, activity_limit: int = 50) -> SupervisorProjectDashboard:
        if not scope.strip() or not workspace.strip() or activity_limit < 1 or activity_limit > 100:
            raise ValueError("invalid dashboard query")
        self.workspaces.get_root(workspace)
        project_state = self.state.get(workspace, scope)
        workflows = self.coding_workflows.list(scope, workspace, coding_limit)
        coding: list[DashboardCodingItem] = []
        attention: list[SupervisorAttentionItem] = []
        history: list[SupervisorAttentionItem] = []
        recovered_plans = {
            attempt.plan_id: workflow.workflow_id
            for workflow in workflows
            if workflow.status == "completed" and workflow.outcome == "verified"
            and workflow.check_trace_ids
            for attempt in workflow.execution_attempts
            if attempt.kind == "check" and attempt.status == "failed"
            and attempt.plan_id != workflow.plan_id
        }
        for workflow in workflows:
            job = self.jobs.get(workflow.workflow_id, scope)
            if job is None or job.workspace != workspace:
                continue
            item = self.guidance.get(job.workflow_id, scope)
            coding.append(DashboardCodingItem(workflow_id=job.workflow_id, status=job.status, objective=job.objective[:500], next_action=job.next_action.action, action_available=job.next_action.allowed, guidance_headline=item.headline[:200] if item else "", blocking_reason=job.blocked_reason, high_impact=item.high_impact if item else False, affected_resources=(item.affected_resources if item else [])[:20], updated_at=job.observed_at))
            category = self._category(job.next_action.action, job.status, job.blocked_reason)
            if job.status == "failed" and not job.next_action.allowed and not job.execution_trace_ids and not getattr(workflow, "execution_attempts", []):
                history.append(SupervisorAttentionItem(id=f"coding:{job.workflow_id}:history", category="informational", source_type="coding_job", source_id=job.workflow_id, workspace=workspace, headline="Read-only attempt stopped", reason="No file changes were executed. The failed task and its report remain available in Coding Jobs.", updated_at=job.observed_at))
            elif category:
                attention.append(self._attention(job, category, item))
        research = [item for item in self.research.list(scope, research_limit) if item.workspace == workspace]
        research_items = [DashboardResearchItem(workflow_id=item.workflow_id, status=item.status, researcher_review_status=item.researcher_review_status, verifier_review_status=item.verifier_review_status, updated_at=item.updated_at) for item in research]
        for item in research_items:
            category = "blocked_failure" if item.status == "failed" else "review_required" if item.status in {"awaiting_review", "awaiting_verifier_review"} else None
            if category:
                attention.append(SupervisorAttentionItem(id=f"research:{item.workflow_id}:{category}", category=category, source_type="research_workflow", source_id=item.workflow_id, workspace=workspace, headline=f"Research workflow {item.workflow_id}", reason=f"Workflow status is {item.status}.", updated_at=item.updated_at))
        plans = [plan for plan in self.plans.list(scope, plan_limit) if plan.workspace in {None, workspace}]
        plan_items = [DashboardPlanItem(plan_id=plan.id, status=plan.status, step_count=len(plan.steps), completed_step_count=sum(step.status in {"completed", "skipped"} for step in plan.steps), failed_step_count=sum(step.status == "failed" for step in plan.steps), updated_at=plan.updated_at) for plan in plans]
        for item in plan_items:
            if item.status == "failed":
                recovered_by = recovered_plans.get(item.plan_id)
                record = SupervisorAttentionItem(id=f"plan:{item.plan_id}:blocked_failure", category="informational" if recovered_by else "blocked_failure", source_type="plan", source_id=item.plan_id, workspace=workspace, headline="Earlier check failure recovered" if recovered_by else f"Plan {item.plan_id} failed", reason=f"Replacement checks passed and workflow {recovered_by} was verified. The original failed plan remains saved." if recovered_by else "The authoritative plan status is failed.", updated_at=item.updated_at, references=[recovered_by] if recovered_by else [])
                (history if recovered_by else attention).append(record)
        approvals = self.tools.list_approval_requests(scope, 100, "pending")
        approval_ids = {
            spec.approval_request_id
            for workflow in workflows
            if (job := self.jobs.get(workflow.workflow_id, scope)) is not None and job.workspace == workspace
            for spec in job.patch_specs
            if spec.approval_request_id
        }
        approvals = [item for item in approvals if item.id in approval_ids]
        if approvals:
            attention.append(SupervisorAttentionItem(id=f"scope:{scope}:approvals", category="approval_required", source_type="approval", source_id=approvals[0].id, workspace=workspace, headline="Approval required", reason=f"{len(approvals)} pending approval request(s).", references=[item.id for item in approvals[:20]]))
        traces = [trace for trace in self.traces.list(scope, activity_limit) if trace]
        activity = [DashboardActivity(id=trace.trace_id, category="execution", status=trace.status, timestamp=trace.created_at, summary=f"{trace.tool} {trace.status}", trace_id=trace.trace_id) for trace in traces]
        attention = self._order_attention(attention)[:50]
        summary = DashboardSummary(active_coding_jobs=sum(item.status not in {"completed", "failed", "cancelled"} for item in coding), active_research_workflows=sum(item.status not in {"completed", "failed", "cancelled"} for item in research_items), active_plans=sum(item.status == "active" for item in plan_items), blocked_items=sum(item.category == "blocked_failure" for item in attention), pending_approvals=sum(item.category == "approval_required" for item in attention), pending_reviews=sum(item.category == "review_required" for item in attention), ready_actions=sum(item.category == "action_ready" for item in attention))
        return SupervisorProjectDashboard(scope=scope, workspace=workspace, generated_at=datetime.now(UTC), project={"workspace": workspace, "available": project_state.available, "branch": project_state.branch, "head": project_state.head, "clean": project_state.clean, "changed_files": project_state.changed_files[:50], "observed_at": project_state.observed_at}, coding=coding, research=research_items, plans=plan_items, recent_activity=activity[:activity_limit], attention=attention, history=history, summary=summary)

    @classmethod
    def _category(cls, action: str, status: str, blocked: str | None) -> AttentionCategory | None:
        if status in {"failed", "cancelled"} or blocked in {"execution failed", "check failed"}:
            return "blocked_failure"
        if action in {"retry_checks", "retry_verifier"}:
            return "action_ready"
        if action in {"review_patch_approvals", "request_patch_approval"}:
            return "approval_required"
        if action in {"review_analysis", "review_proposal", "review_specs", "review_verifier"}:
            return "review_required"
        if action == "create_proposal":
            return "user_input_required"
        if action in {"start_analysis", "convert_proposal", "execute_patches", "execute_checks", "start_verifier", "request_revision"}:
            return "action_ready"
        return None

    @staticmethod
    def _attention(job: Any, category: AttentionCategory, guidance: Any) -> SupervisorAttentionItem:
        return SupervisorAttentionItem(id=f"coding:{job.workflow_id}:{job.next_action.action}", category=category, source_type="coding_job", source_id=job.workflow_id, workspace=job.workspace, headline=(guidance.headline if guidance else f"Coding workflow {job.workflow_id}")[:200], reason=job.next_action.reason[:500], next_action=job.next_action.action, high_impact=guidance.high_impact if guidance else False, updated_at=job.observed_at, references=job.next_action.references[:20])

    @classmethod
    def _order_attention(cls, items: list[SupervisorAttentionItem]) -> list[SupervisorAttentionItem]:
        unique = {item.id: item for item in items}
        return sorted(unique.values(), key=lambda item: (cls.CATEGORY_ORDER[item.category], item.updated_at or datetime.min.replace(tzinfo=UTC), item.id))
