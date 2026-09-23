from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from app.journal.store import ActionJournal
from app.plans.service import PlanService
from app.tools.registry import ToolRegistry


class PlanExecutionError(RuntimeError):
    pass


class PlanExecutionCoordinator:
    def __init__(self, plans: PlanService, tools: ToolRegistry, journal: ActionJournal | None = None) -> None:
        self._plans = plans
        self._tools = tools
        self._journal = journal

    def execute_once(
        self,
        *,
        scope: str,
        plan_id: str,
        step_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        verify: Callable[[Any], bool] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        plan = self._plans.get(plan_id, scope)
        if plan is None:
            raise PlanExecutionError("plan not found")
        if plan.status != "active":
            raise PlanExecutionError("plan is not active")
        step = next((item for item in plan.steps if item.id == step_id), None)
        if step is None:
            raise PlanExecutionError("step not found")
        if step.status != "pending" or self._plans.next_step(plan_id, scope).id != step_id:
            raise PlanExecutionError("step is not actionable")
        tool = self._tools.get(tool_name)
        if tool.permission != "read":
            raise PlanExecutionError("tool permission denied")
        trace_id = str(uuid4())
        self._plans.transition(plan_id, step_id, "in_progress", scope, trace_id)
        try:
            result = self._tools.execute(tool_name, **arguments)
            if verify is not None and not verify(result):
                raise PlanExecutionError("verification failed")
        except Exception as exc:
            if self._journal is not None and session_id is not None:
                self._journal.record(session_id=session_id, provider="plan", model="coordinator", tool=tool_name,
                                     permission=tool.permission, arguments={"scope": scope, "plan_id": plan_id, "step_id": step_id, "trace_id": trace_id},
                                     status="error", error="verification failed" if isinstance(exc, PlanExecutionError) else "tool execution failed")
            self._plans.transition(plan_id, step_id, "failed", scope, trace_id)
            if isinstance(exc, PlanExecutionError):
                raise
            raise PlanExecutionError("tool execution failed") from exc
        try:
            if self._journal is not None and session_id is not None:
                self._journal.record(session_id=session_id, provider="plan", model="coordinator", tool=tool_name,
                                     permission=tool.permission, arguments={"scope": scope, "plan_id": plan_id, "step_id": step_id, "trace_id": trace_id},
                                     status="success", result_preview=str(result)[:1000])
            self._plans.transition(plan_id, step_id, "completed", scope, trace_id)
        except Exception as exc:
            self._plans.transition(plan_id, step_id, "failed", scope, trace_id)
            raise PlanExecutionError("execution journal failed") from exc
        return {"trace_id": trace_id, "tool": tool_name, "result": result}
