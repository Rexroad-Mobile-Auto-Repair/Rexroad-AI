from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from app.journal.store import ActionJournal
from app.plans.service import PlanService
from app.plans.verification import VerificationPolicy
from app.tools.output_policy import sanitize_output
from app.tools.registry import ToolApproval, ToolAuthorization, ToolRegistry


def _result_preview(tool_name: str, result: Any) -> str | None:
    if result is None:
        return None
    value = sanitize_output(result)
    if tool_name == "workspace.run_check" and isinstance(value, dict):
        value = {key: value[key] for key in ("check_id", "status", "passed", "exit_code", "timed_out", "duration_ms") if key in value}
        # Keep this dictionary parseable even when pytest emits a long traceback.
        value["stdout"] = str(result.get("stdout", ""))[:100]
        value["stderr"] = str(result.get("stderr", ""))[:100]
        return str(value)
    return str(value)[:1000]


class PlanExecutionError(RuntimeError):
    def __init__(self, message: str, trace_id: str | None = None) -> None:
        super().__init__(message)
        self.trace_id = trace_id


class PlanExecutionCoordinator:
    def __init__(self, plans: PlanService, tools: ToolRegistry, journal: ActionJournal | None = None, on_success: Callable[[str, str, str], None] | None = None) -> None:
        self._plans, self._on_success = plans, on_success
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
        trace_metadata: dict[str, Any] | None = None,
        allow_parallel: bool = False,
    ) -> dict[str, Any]:
        plan = self._plans.get(plan_id, scope)
        if plan is None:
            raise PlanExecutionError("plan not found")
        if plan.status != "active":
            raise PlanExecutionError("plan is not active")
        step = next((item for item in plan.steps if item.id == step_id), None)
        if step is None:
            raise PlanExecutionError("step not found")
        if step.status != "pending" or (not allow_parallel and self._plans.next_step(plan_id, scope).id != step_id):
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
        result = None
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
                journal_arguments = {"scope": scope, "plan_id": plan_id, "step_id": step_id, "trace_id": trace_id, "approval_validated": approval_validated, "verification_status": verification_status, "verification_reason": verification_reason, **(trace_metadata or {})}
                self._journal.record(session_id=session_id, provider="plan", model="coordinator", tool=tool_name,
                                     permission=tool.permission, arguments=journal_arguments,
                                     status="error", error="verification failed" if isinstance(exc, PlanExecutionError) else "tool execution failed",
                                     result_preview=_result_preview(tool_name, result) if tool_name == "workspace.run_check" else None)
            self._plans.transition(plan_id, step_id, "failed", scope, trace_id)
            if isinstance(exc, PlanExecutionError):
                exc.trace_id = trace_id
                raise
            raise PlanExecutionError("tool execution failed", trace_id=trace_id) from exc
        try:
            if self._journal is not None and session_id is not None:
                journal_arguments = {"scope": scope, "plan_id": plan_id, "step_id": step_id, "trace_id": trace_id, "approval_validated": approval_validated, "verification_status": verification_status, "verification_reason": verification_reason, **(trace_metadata or {})}
                self._journal.record(session_id=session_id, provider="plan", model="coordinator", tool=tool_name,
                                     permission=tool.permission, arguments=journal_arguments,
                                     status="success", result_preview=_result_preview(tool_name, result))
            self._plans.transition(plan_id, step_id, "completed", scope, trace_id)
            if self._on_success is not None:
                try:
                    self._on_success(plan.workspace or "", scope, "verified_execution")
                except Exception as hook_error:  # noqa: BLE001 - optional snapshot hook is best effort
                    _ = hook_error
        except Exception as exc:
            self._plans.transition(plan_id, step_id, "failed", scope, trace_id)
            raise PlanExecutionError("execution journal failed") from exc
        return {"trace_id": trace_id, "tool": tool_name, "result": result}
