"""Read-only specialized analysis through existing worker and plan services."""
from __future__ import annotations

import asyncio

from app.autonomy.synthesis import from_subagent
from app.plans.service import PlanService
from app.subagents import SubAgentService, SupervisorDispatchRequest


class PlannedAnalysis:
    def __init__(self, plans: PlanService, agents: SubAgentService) -> None:
        self._plans, self._agents = plans, agents

    def run(self, *, plan_id: str, step_id: str, scope: str, workspace: str) -> dict:
        plan = self._plans.get(plan_id, scope)
        step = next((item for item in plan.steps if item.id == step_id), None) if plan else None
        if plan is None or plan.status != "active" or plan.workspace != workspace or step is None or step.status != "in_progress" or step.metadata.get("mutation_required"):
            raise ValueError("analysis task is not authorized")
        role = step.metadata.get("worker")
        if role not in {"code_analyst", "test_analyst", "architecture_analyst", "security_analyst"}:
            raise ValueError("not a specialized analysis task")
        if not self._agents.provider_backed:
            raise ValueError("provider-backed analysis unavailable")
        request = SupervisorDispatchRequest(
            worker_profile="code_analyst", scope=scope, workspace=workspace,
            instruction=f"Inspect code as {role}: {step.metadata.get('objective', step.title)}\nExpected evidence: {step.metadata.get('expected_evidence', '')}"[:4000],
            plan_id=plan_id, step_id=step_id,
            **self._agents.workflow_options("code_analyst"),
        )
        result = asyncio.run(self._agents.dispatch(request, self._agents.authorize_dispatch(request)))
        audits = [item for item in self._agents.audits(scope, 100) if item.task_id == result.task_id]
        if result.status != "completed" or not audits or not any(item.get("status") == "success" for item in audits[0].tool_usage):
            raise ValueError("analysis produced no verified source evidence")
        contract = from_subagent(result, [audits[0].dispatch_id])
        contract.worker_type = role
        contract.findings = [contract.summary] if contract.summary else []
        contract.files_examined = sorted({item["arguments"]["relative_path"] for item in audits[0].tool_usage if item.get("status") == "success" and item.get("arguments", {}).get("relative_path")})[:50]
        return contract.model_dump()
