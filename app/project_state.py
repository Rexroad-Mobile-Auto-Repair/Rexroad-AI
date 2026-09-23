from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.memory.service import MemoryService
from app.plans.service import PlanService
from app.plans.traces import ExecutionTrace, ExecutionTraceService
from app.policy.workspaces import WorkspaceRegistry
from app.tools.git import GitCommandError, ReadOnlyGit


class ProjectState(BaseModel):
    workspace: str
    available: bool
    scope: str
    branch: str | None
    head: str | None
    clean: bool | None
    changed_files: list[str]
    active_plans: list[dict]
    next_steps: list[dict]
    unresolved_tasks: list[dict]
    recent_traces: list[ExecutionTrace]
    observed_at: datetime


class ProjectStateService:
    def __init__(self, workspaces: WorkspaceRegistry, git: ReadOnlyGit, memories: MemoryService, plans: PlanService, traces: ExecutionTraceService) -> None:
        self._workspaces, self._git, self._memories, self._plans, self._traces = workspaces, git, memories, plans, traces

    def get(self, workspace: str, scope: str) -> ProjectState:
        info = self._workspaces.inspect(workspace)
        branch = head = None
        clean: bool | None = None
        changed: list[str] = []
        try:
            branch = self._git.branch(workspace)
            head = self._git.show(workspace, "HEAD").split()[0]
            status = self._git.status(workspace)
            lines = status.splitlines()
            changed = [line[3:] for line in lines[1:] if len(line) > 3]
            clean = not changed
        except GitCommandError:
            pass
        active = [plan for plan in self._plans.list(scope, 100) if plan.status == "active" and (plan.workspace is None or plan.workspace == workspace)]
        return ProjectState(workspace=workspace, available=info.available, scope=scope, branch=branch, head=head, clean=clean, changed_files=changed, active_plans=[plan.model_dump(mode="json") for plan in active], next_steps=[{"plan_id": plan.id, "step": self._plans.next_step(plan.id, scope).model_dump(mode="json")} for plan in active if self._plans.next_step(plan.id, scope)], unresolved_tasks=[item.model_dump(mode="json") for item in self._memories.list(scope, category="task", status="active", limit=100)], recent_traces=self._traces.list(scope), observed_at=datetime.now().astimezone())
