from __future__ import annotations

from typing import Any

from app.autonomy.bridge import TaskExecutionSpec
from app.coding_workflows import CodingWorkflowCreate, CodingWorkflowService
from app.supervisor_workflows import ResearchVerifyWorkflowCreate, SupervisorResearchVerifyWorkflow


class PlannedWorkerDispatcher:
    """Adapter from planned worker metadata to registered Rexroad workflows."""

    def __init__(self, *, coding: CodingWorkflowService | None = None, research: SupervisorResearchVerifyWorkflow | None = None) -> None:
        self._coding = coding
        self._research = research

    def dispatch(self, spec: TaskExecutionSpec) -> dict[str, Any]:
        if spec.worker == "supervised_coding":
            if self._coding is None:
                return {"status": "waiting_for_unavailable_worker", "reason": "supervised coding workflow unavailable"}
            existing = next((item for item in self._coding.list(spec.scope, spec.workspace, 100) if item.parent_plan_id == spec.plan_id and item.parent_step_id == spec.step_id), None) if hasattr(self._coding, "list") else None
            if existing is not None:
                return {"status": self._coding_status(existing.status), "workflow_id": existing.workflow_id, "workflow_status": existing.status}
            workflow = self._coding.create(CodingWorkflowCreate(scope=spec.scope, workspace=spec.workspace or "", instruction=spec.objective, plan_id=spec.plan_id, step_id=spec.step_id))
            return {"status": self._coding_status(workflow.status), "workflow_id": workflow.workflow_id, "workflow_status": workflow.status}
        if spec.worker in {"researcher", "verifier"}:
            if self._research is None:
                return {"status": "waiting_for_unavailable_worker", "reason": "research workflow unavailable"}
            existing = next((item for item in self._research.list(spec.scope, 100) if item.plan_id == spec.plan_id and item.step_id == spec.step_id), None) if hasattr(self._research, "list") else None
            if existing is not None:
                return {"status": "waiting_for_worker_dispatch", "workflow_id": existing.workflow_id, "workflow_status": existing.status}
            workflow = self._research.create(ResearchVerifyWorkflowCreate(scope=spec.scope, workspace=spec.workspace, instruction=spec.objective, plan_id=spec.plan_id, step_id=spec.step_id))
            return {"status": "waiting_for_worker_dispatch", "workflow_id": workflow.workflow_id, "workflow_status": workflow.status}
        if spec.worker in {"mcp", "skill"}:
            return {"status": "waiting_for_unavailable_worker", "reason": f"no safe planned {spec.worker} dispatcher is registered"}
        return {"status": "waiting_for_unavailable_worker", "reason": "worker route unavailable"}

    @staticmethod
    def _coding_status(status: str) -> str:
        if status in {"completed"}:
            return "completed"
        if status in {"failed", "cancelled"}:
            return status
        if status in {"awaiting_analysis", "awaiting_analysis_review", "analysis_accepted", "implementation_ready", "implementing", "awaiting_checks", "awaiting_verification", "awaiting_verifier_review"}:
            return "waiting_for_workflow"
        return "waiting_for_approval"
