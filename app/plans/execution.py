from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from app.journal.store import ActionJournal
from app.plans.service import PlanService
from app.plans.verification import VerificationPolicy
from app.tools.registry import ToolApproval, ToolAuthorization, ToolRegistry


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
        authorization: ToolAuthorization,
        approval: ToolApproval | None = None,
        arguments: dict[str, Any],
        verify: Callable[[Any], bool] | None = None,
        verification_policy: VerificationPolicy | None = None,
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
        if not self._tools.validate_authorization(authorization, tool_name, scope, session_id):
            raise PlanExecutionError("tool permission denied")
        if tool.high_impact and approval is None:
            raise PlanExecutionError("tool approval required")
        if approval is not None and not self._tools.consume_approval(approval, authorization, tool_name, scope, session_id, arguments):
            raise PlanExecutionError("invalid tool approval")
        approval_validated = tool.high_impact
        trace_id = str(uuid4())
        verification_status = "not_run"
        verification_reason = "not_requested"
        self._plans.transition(plan_id, step_id, "in_progress", scope, trace_id)
        try:
            result = self._tools.execute(tool_name, **arguments)
            verification = verification_policy.verify(result) if verification_policy is not None else None
            if verification is not None and not verification.passed:
                verification_status = "failed"
                verification_reason = verification.reason
                raise PlanExecutionError("verification failed")
            if verification is not None:
                verification_status = "passed"
                verification_reason = verification.reason
            if verify is not None and not verify(result):
                verification_status = "failed"
                verification_reason = "callable_failed"
                raise PlanExecutionError("verification failed")
            if verify is not None:
                verification_status = "passed"
                verification_reason = "callable_passed"
        except Exception as exc:
            if self._journal is not None and session_id is not None:
                self._journal.record(session_id=session_id, provider="plan", model="coordinator", tool=tool_name,
                                     permission=tool.permission, arguments={"scope": scope, "plan_id": plan_id, "step_id": step_id, "trace_id": trace_id, "approval_validated": approval_validated, "verification_status": verification_status, "verification_reason": verification_reason},
                                     status="error", error="verification failed" if isinstance(exc, PlanExecutionError) else "tool execution failed")
            self._plans.transition(plan_id, step_id, "failed", scope, trace_id)
            if isinstance(exc, PlanExecutionError):
                raise
            raise PlanExecutionError("tool execution failed") from exc
        try:
            if self._journal is not None and session_id is not None:
                self._journal.record(session_id=session_id, provider="plan", model="coordinator", tool=tool_name,
                                     permission=tool.permission, arguments={"scope": scope, "plan_id": plan_id, "step_id": step_id, "trace_id": trace_id, "approval_validated": approval_validated, "verification_status": verification_status, "verification_reason": verification_reason},
                                     status="success", result_preview=str(result)[:1000])
            self._plans.transition(plan_id, step_id, "completed", scope, trace_id)
        except Exception as exc:
            self._plans.transition(plan_id, step_id, "failed", scope, trace_id)
            raise PlanExecutionError("execution journal failed") from exc
        return {"trace_id": trace_id, "tool": tool_name, "result": result}
