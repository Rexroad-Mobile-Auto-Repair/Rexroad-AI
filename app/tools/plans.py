from __future__ import annotations

from typing import Any

from app.plans.models import StepStatus
from app.plans.service import PlanService


class PlanTools:
    def __init__(self, plans: PlanService) -> None:
        self._plans = plans

    def list(self, scope: str, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        plans = self._plans.list(scope, limit)
        if status is not None:
            plans = [plan for plan in plans if plan.status == status]
        return [plan.model_dump(mode="json") for plan in plans]

    def get(self, scope: str, plan_id: str) -> dict[str, Any]:
        plan = self._plans.get(plan_id, scope)
        if plan is None:
            raise ValueError("plan not found")
        return plan.model_dump(mode="json")

    def next_step(self, scope: str, plan_id: str) -> dict[str, Any] | None:
        plan = self._plans.get(plan_id, scope)
        if plan is None:
            raise ValueError("plan not found")
        step = self._plans.next_step(plan_id, scope)
        return step.model_dump(mode="json") if step else None

    def update_step(self, scope: str, plan_id: str, step_id: str, status: StepStatus, reference: str | None = None) -> dict[str, Any]:
        plan = self._plans.transition(plan_id, step_id, status, scope, reference)
        if plan is None:
            raise ValueError("plan not found")
        return plan.model_dump(mode="json")
