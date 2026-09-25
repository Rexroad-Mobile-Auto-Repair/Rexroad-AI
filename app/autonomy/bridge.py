from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from app.autonomy.service import AutonomousContinuationService
from app.plans.continuation import StepExecutionSpec
from app.plans.models import PlanStep, ProjectPlan
from app.plans.service import PlanService
from app.plans.verification import ResultPresentPolicy
from app.tools.registry import ToolAuthorization


class TaskExecutionSpec(BaseModel):
    plan_id: str
    step_id: str
    objective: str
    workspace: str | None
    scope: str
    dependencies: list[str] = Field(default_factory=list)
    worker: str
    tool_category: str
    tool_name: str | None = None
    expected_evidence: str
    verification: str
    mutation_required: bool
    approval_status: str = "not_required"
    execution_status: str = "pending"
    worker_reference: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlannedTaskExecutionBridge:
    """Resolve persisted planner metadata into existing authorized execution calls."""

    WORKERS: ClassVar[frozenset[str]] = frozenset({"direct", "researcher", "code_analyst", "verifier", "supervised_coding", "mcp", "skill"})
    TOOL_CATEGORIES: ClassVar[frozenset[str]] = frozenset({"read", "repo_map", "navigation", "git_status", "filesystem_read", "write", "execute", "mcp", "skill", "supervised_coding", "research"})

    def __init__(self, plans: PlanService, continuation: AutonomousContinuationService, dispatcher: Any | None = None) -> None:
        self._plans = plans
        self._continuation = continuation
        self._dispatcher = dispatcher

    def build_spec(self, plan: ProjectPlan, step: PlanStep) -> TaskExecutionSpec:
        metadata = step.metadata
        worker = str(metadata.get("worker", "direct"))
        category = str(metadata.get("tool_category", "read"))
        if worker not in self.WORKERS:
            raise ValueError("unknown worker category")
        if category not in self.TOOL_CATEGORIES:
            raise ValueError("unknown tool category")
        tool_name = self._tool_name(category, worker)
        mutation = bool(metadata.get("mutation_required", False))
        approval = "required" if mutation else "not_required"
        return TaskExecutionSpec(
            plan_id=plan.id, step_id=step.id, objective=str(metadata.get("objective", step.title))[:1000],
            workspace=plan.workspace, scope=plan.scope,
            dependencies=self._dependencies(metadata, plan.steps), worker=worker, tool_category=category,
            tool_name=tool_name, expected_evidence=str(metadata.get("expected_evidence", "execution result"))[:500],
            verification=str(metadata.get("verification", "result present"))[:500], mutation_required=mutation,
            approval_status=approval,
        )

    def resolve(self, plan: ProjectPlan, step: PlanStep, authorizations: dict[str, ToolAuthorization] | None = None) -> tuple[TaskExecutionSpec, StepExecutionSpec | None]:
        spec = self.build_spec(plan, step)
        if (spec.worker != "direct" or spec.mutation_required) and self._dispatcher is not None:
            result = self._dispatcher.dispatch(spec)
            return spec.model_copy(update={"execution_status": result.get("status", "waiting_for_worker_dispatch"), "worker_reference": result.get("workflow_id")}), None
        if spec.mutation_required:
            return spec.model_copy(update={"execution_status": "waiting_for_approval"}), None
        if spec.tool_name is None:
            return spec.model_copy(update={"execution_status": "waiting_for_worker_dispatch"}), None
        authorization = (authorizations or {}).get(spec.tool_name)
        if authorization is None:
            return spec.model_copy(update={"execution_status": "waiting_for_authorization"}), None
        arguments = self._arguments(spec)
        return spec, StepExecutionSpec(tool_name=spec.tool_name, authorization=authorization, arguments=arguments, verification_policy=ResultPresentPolicy())

    def _tool_name(self, category: str, worker: str) -> str | None:
        if category in {"repo_map", "navigation"}: return "workspace.repo_map"
        if category == "git_status": return "git.status"
        if category == "filesystem_read": return "filesystem.read"
        if category == "read" and worker in {"direct", "code_analyst", "verifier"}: return "workspace.repo_map"
        return None

    @staticmethod
    def _dependencies(metadata: dict[str, Any], steps: list[PlanStep]) -> list[str]:
        raw = metadata.get("depends_on", metadata.get("dependencies", []))
        dependencies = {item for item in raw if isinstance(item, str)} if isinstance(raw, list) else set()
        positions = metadata.get("depends_on_positions", [])
        if isinstance(positions, list):
            dependencies.update(steps[index].id for index in positions if isinstance(index, int) and 0 <= index < len(steps))
        return sorted(dependencies)

    @staticmethod
    def _arguments(spec: TaskExecutionSpec) -> dict[str, Any]:
        if spec.tool_name == "workspace.repo_map":
            return {"workspace": spec.workspace, "operation": "map"}
        if spec.tool_name == "git.status":
            return {"workspace": spec.workspace}
        return {"workspace": spec.workspace, "relative_path": "."}
