from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.plans.execution import PlanExecutionCoordinator, PlanExecutionError
from app.plans.service import PlanService
from app.plans.verification import VerificationPolicy
from app.tools.registry import ToolApproval, ToolAuthorization


@dataclass(frozen=True)
class StepExecutionSpec:
    tool_name: str
    authorization: ToolAuthorization
    arguments: dict[str, Any]
    approval: ToolApproval | None = None
    verification_policy: VerificationPolicy | None = None
    session_id: str | None = None


class PlanContinuationCoordinator:
    def __init__(self, plans: PlanService, executor: PlanExecutionCoordinator) -> None:
        self._plans = plans
        self._executor = executor

    def continue_plan(
        self,
        *,
        scope: str,
        plan_id: str,
        max_steps: int,
        specifications: dict[str, StepExecutionSpec],
    ) -> dict[str, Any]:
        if not scope.strip() or max_steps < 1 or max_steps > 10:
            raise ValueError("invalid continuation scope or max_steps")
        plan = self._plans.get(plan_id, scope)
        if plan is None:
            raise PlanExecutionError("plan not found")
        attempted: list[str] = []
        completed: list[str] = []
        stopped_reason = "max_steps"
        for _ in range(max_steps):
            plan = self._plans.get(plan_id, scope)
            if plan is None:
                raise PlanExecutionError("plan not found")
            if plan.status != "active":
                stopped_reason = f"plan_{plan.status}"
                break
            step = self._plans.next_step(plan_id, scope)
            if step is None:
                stopped_reason = "plan_completed"
                break
            spec = specifications.get(step.id)
            if spec is None:
                stopped_reason = "missing_execution_specification"
                break
            attempted.append(step.id)
            try:
                self._executor.execute_once(
                    scope=scope,
                    plan_id=plan_id,
                    step_id=step.id,
                    tool_name=spec.tool_name,
                    authorization=spec.authorization,
                    approval=spec.approval,
                    arguments=spec.arguments,
                    verification_policy=spec.verification_policy,
                    session_id=spec.session_id,
                )
            except PlanExecutionError as exc:
                stopped_reason = str(exc)
                break
            completed.append(step.id)
        else:
            stopped_reason = "max_steps"
        final = self._plans.get(plan_id, scope)
        return {"plan_id": plan_id, "scope": scope, "attempted": attempted, "completed": completed, "stopped_reason": stopped_reason, "plan_status": final.status if final else "missing"}
