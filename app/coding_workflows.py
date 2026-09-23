from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.plans.bridge import TrustedExecutionBridge
from app.plans.execution import PlanExecutionCoordinator
from app.plans.models import PlanCreate, PlanStepCreate
from app.plans.service import PlanService
from app.plans.specs import ExecutionSpecService
from app.policy.workspaces import WorkspaceRegistry
from app.subagents import SubAgentService, SupervisorDispatchRequest
from app.tools.git import ReadOnlyGit
from app.tools.registry import ToolRegistry


class PatchAction(BaseModel):
    relative_path: str
    expected_text: str
    replacement: str
    verification: dict[str, Any] = Field(default_factory=lambda: {"type": "result_present"})


class CheckAction(BaseModel):
    check_id: str
    targets: list[str] = Field(default_factory=list)


class CodingWorkflowCreate(BaseModel):
    scope: str
    workspace: str
    instruction: str = Field(min_length=1, max_length=4000)
    parent_session_id: str | None = None
    plan_id: str | None = None
    step_id: str | None = None


class CodingWorkflow(BaseModel):
    workflow_id: str
    scope: str
    workspace: str
    instruction: str
    status: str
    analyst_task_id: str | None = None
    analyst_dispatch_id: str | None = None
    analyst_review_status: str | None = None
    plan_id: str | None = None
    mutation_spec_ids: list[str] = Field(default_factory=list)
    mutation_trace_ids: list[str] = Field(default_factory=list)
    check_spec_ids: list[str] = Field(default_factory=list)
    check_trace_ids: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    verifier_task_id: str | None = None
    verifier_dispatch_id: str | None = None
    verifier_review_status: str | None = None
    outcome: str | None = None
    baseline_status: str | None = None
    baseline_branch: str | None = None
    baseline_head: str | None = None
    baseline_dirty_files: list[str] = Field(default_factory=list)
    parent_session_id: str | None = None
    parent_plan_id: str | None = None
    parent_step_id: str | None = None
    created_at: datetime
    updated_at: datetime


class CodingWorkflowService:
    MAX_ACTIONS = 5
    def __init__(self, database_path: str | Path, workspaces: WorkspaceRegistry, git: ReadOnlyGit, agents: SubAgentService, plans: PlanService, specs: ExecutionSpecService, bridge: TrustedExecutionBridge, executor: PlanExecutionCoordinator, tools: ToolRegistry) -> None:
        self.path = Path(database_path); self.workspaces = workspaces; self.git = git; self.agents = agents; self.plans = plans; self.specs = specs; self.bridge = bridge; self.executor = executor; self.tools = tools
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS coding_workflows (workflow_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)")

    def create(self, request: CodingWorkflowCreate) -> CodingWorkflow:
        if not request.scope.strip(): raise ValueError("scope required")
        self.workspaces.get_root(request.workspace)
        now = datetime.now(UTC)
        item = CodingWorkflow(workflow_id=str(uuid4()), scope=request.scope, workspace=request.workspace, instruction=request.instruction, status="awaiting_analysis", parent_session_id=request.parent_session_id, parent_plan_id=request.plan_id, parent_step_id=request.step_id, created_at=now, updated_at=now)
        self._save(item); return item

    def get(self, workflow_id: str, scope: str) -> CodingWorkflow | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT payload_json FROM coding_workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
        if not row: return None
        item = CodingWorkflow.model_validate_json(row[0]); return item if item.scope == scope else None

    async def start_analysis(self, workflow_id: str, scope: str) -> CodingWorkflow:
        item = self._require(workflow_id, scope)
        if item.status != "awaiting_analysis": raise ValueError("analysis cannot start")
        request = SupervisorDispatchRequest(worker_profile="code_analyst", scope=scope, workspace=item.workspace, instruction=f"Inspect coding objective: {item.instruction}", parent_session_id=item.parent_session_id, plan_id=item.parent_plan_id, step_id=item.parent_step_id)
        auth = self.agents.authorize_dispatch(request); result = await self.agents.dispatch(request, auth)
        audit = self.agents.audits(scope, 100); dispatch_id = audit[0].dispatch_id if audit and audit[0].task_id == result.task_id else None
        item = item.model_copy(update={"analyst_task_id": result.task_id, "analyst_dispatch_id": dispatch_id, "status": "awaiting_analysis_review" if result.status == "completed" else "failed"})
        self._save(item); return item

    def prepare(self, workflow_id: str, scope: str, patches: list[PatchAction]) -> CodingWorkflow:
        item = self._require(workflow_id, scope)
        if item.status != "awaiting_analysis_review" or not item.analyst_task_id: raise ValueError("accepted analysis required")
        review = self.agents.get_review(item.analyst_task_id, scope)
        if review is None or review.status != "accepted": raise ValueError("accepted analysis required")
        if not patches or len(patches) > self.MAX_ACTIONS: raise ValueError("at most five patches")
        status = self.git.status(item.workspace); branch = self.git.branch(item.workspace)
        dirty = [line[3:].strip().split(" -> ")[-1] for line in status.splitlines() if line and not line.startswith("##")]
        if any(p.relative_path in dirty for p in patches):
            raise ValueError("cannot target pre-existing dirty file")
        plan = self.plans.create(PlanCreate(scope=scope, workspace=item.workspace, goal=item.instruction, steps=[PlanStepCreate(title=f"Apply {p.relative_path}", metadata={"coding": True}) for p in patches]))
        ids=[]
        for step, patch in zip(plan.steps, patches):
            spec = self.specs.create(scope=scope, plan_id=plan.id, step_id=step.id, tool_name="filesystem.apply_patch", arguments={"workspace": item.workspace, "relative_path": patch.relative_path, "expected_text": patch.expected_text, "replacement": patch.replacement}, verification=patch.verification, session_id=item.parent_session_id)
            ids.append(spec.id)
        item = item.model_copy(update={"plan_id": plan.id, "mutation_spec_ids": ids, "status": "implementation_ready", "baseline_status": status[:4000], "baseline_branch": branch, "baseline_head": self.git.show(item.workspace, "HEAD")[:80], "baseline_dirty_files": sorted(dirty)})
        self._save(item); return item

    def ready_specs(self, workflow_id: str, scope: str) -> CodingWorkflow:
        item = self._require(workflow_id, scope)
        if item.status != "implementation_ready": raise ValueError("implementation is not ready")
        for spec_id in item.mutation_spec_ids: self.specs.mark_ready(spec_id, scope)
        item = item.model_copy(update={"status": "implementing"}); self._save(item); return item

    def execute_implementation(self, workflow_id: str, scope: str, approval_request_ids: list[str]) -> CodingWorkflow:
        item = self._require(workflow_id, scope)
        if item.status not in {"implementation_ready", "implementing"}: raise ValueError("implementation unavailable")
        if len(approval_request_ids) != len(item.mutation_spec_ids): raise ValueError("approval required for every patch")
        if item.status == "implementation_ready": self.ready_specs(workflow_id, scope); item = self._require(workflow_id, scope)
        traces = []
        for spec_id, approval_id in zip(item.mutation_spec_ids, approval_request_ids):
            runtime, authorization, approval = self.bridge.prepare(scope=scope, spec_id=spec_id, approval_request_id=approval_id)
            spec = self.specs.get(spec_id, scope)
            result = self.executor.execute_once(scope=scope, plan_id=self._require(workflow_id, scope).plan_id or "", step_id=spec.step_id, tool_name=runtime.tool_name, authorization=authorization, approval=approval, arguments=runtime.arguments, verification_policy=runtime.verification_policy, session_id=runtime.session_id)
            traces.append(result["trace_id"])
        item = self._require(workflow_id, scope).model_copy(update={"mutation_trace_ids": traces, "status": "awaiting_checks"}); self._save(item); return item

    def prepare_checks(self, workflow_id: str, scope: str, checks: list[CheckAction]) -> CodingWorkflow:
        item = self._require(workflow_id, scope)
        if item.status != "awaiting_checks": raise ValueError("checks are not available")
        if not checks or len(checks) > self.MAX_ACTIONS: raise ValueError("at most five checks")
        plan = self.plans.create(PlanCreate(scope=scope, workspace=item.workspace, goal=f"Checks: {item.instruction}", steps=[PlanStepCreate(title=f"Run {c.check_id}", metadata={"coding_check": True}) for c in checks]))
        ids = []
        for step, check in zip(plan.steps, checks):
            if check.check_id not in {"pytest", "ruff", "git_diff_check"}: raise ValueError("unsupported check")
            spec = self.specs.create(scope=scope, plan_id=plan.id, step_id=step.id, tool_name="workspace.run_check", arguments={"workspace": item.workspace, "check_id": check.check_id, "targets": check.targets}, verification={"type": "field_equals", "field": "passed", "expected": True}, session_id=item.parent_session_id)
            ids.append(spec.id)
        item = item.model_copy(update={"plan_id": plan.id, "check_spec_ids": ids, "status": "awaiting_checks"}); self._save(item); return item

    def execute_checks(self, workflow_id: str, scope: str) -> CodingWorkflow:
        item = self._require(workflow_id, scope)
        if not item.check_spec_ids: raise ValueError("checks are not prepared")
        traces = []
        for spec_id in item.check_spec_ids:
            self.specs.mark_ready(spec_id, scope)
            runtime, authorization, approval = self.bridge.prepare(scope=scope, spec_id=spec_id)
            spec = self.specs.get(spec_id, scope)
            try:
                result = self.executor.execute_once(scope=scope, plan_id=item.plan_id or "", step_id=spec.step_id, tool_name=runtime.tool_name, authorization=authorization, approval=approval, arguments=runtime.arguments, verification_policy=runtime.verification_policy, session_id=runtime.session_id)
            except Exception as exc:
                failed = self._require(workflow_id, scope).model_copy(update={"status": "failed", "outcome": "check_failed"}); self._save(failed); raise ValueError("check failed") from exc
            traces.append(result["trace_id"])
        item = self._require(workflow_id, scope).model_copy(update={"check_trace_ids": traces, "status": "awaiting_verification"}); self._save(item); return item

    async def start_verification(self, workflow_id: str, scope: str) -> CodingWorkflow:
        item = self._require(workflow_id, scope)
        if item.status != "awaiting_verification": raise ValueError("verification is not available")
        request = SupervisorDispatchRequest(worker_profile="verifier", scope=scope, workspace=item.workspace, instruction=f"Verify objective: {item.instruction[:1600]} Files changed: {', '.join(item.changed_files[:20])}", parent_session_id=item.parent_session_id, plan_id=item.parent_plan_id, step_id=item.parent_step_id)
        auth = self.agents.authorize_dispatch(request); result = await self.agents.dispatch(request, auth)
        audits = self.agents.audits(scope, 100); dispatch_id = audits[0].dispatch_id if audits and audits[0].task_id == result.task_id else None
        updated = self._require(workflow_id, scope).model_copy(update={"verifier_task_id": result.task_id, "verifier_dispatch_id": dispatch_id, "verifier_review_status": "pending", "status": "awaiting_verifier_review" if result.status == "completed" else "failed"}); self._save(updated); return updated

    def review_verifier(self, workflow_id: str, scope: str, status: str, reviewer_session_id: str | None = None, note: str | None = None) -> CodingWorkflow:
        item = self._require(workflow_id, scope)
        if item.status != "awaiting_verifier_review" or not item.verifier_task_id: raise ValueError("verifier review unavailable")
        review = self.agents.review(item.verifier_task_id, scope, status, reviewer_session_id, note)
        updated = item.model_copy(update={"verifier_review_status": review.status, "status": "completed" if status == "accepted" else "failed", "outcome": "verified" if status == "accepted" else "rejected"}); self._save(updated); return updated

    def cancel(self, workflow_id: str, scope: str, reason: str | None = None) -> CodingWorkflow:
        item = self._require(workflow_id, scope)
        if item.status in {"completed", "failed", "cancelled"}: return item
        updated = item.model_copy(update={"status": "cancelled", "outcome": "cancelled"}); self._save(updated); return updated

    def _require(self, workflow_id: str, scope: str) -> CodingWorkflow:
        item = self.get(workflow_id, scope)
        if item is None: raise ValueError("workflow not found")
        return item

    def _save(self, item: CodingWorkflow) -> None:
        with sqlite3.connect(self.path) as db: db.execute("INSERT OR REPLACE INTO coding_workflows VALUES (?, ?)", (item.workflow_id, item.model_dump_json()))
