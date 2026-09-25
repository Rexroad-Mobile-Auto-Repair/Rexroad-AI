from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.plans.continuation import StepExecutionSpec
from app.plans.execution import PlanExecutionCoordinator, PlanExecutionError
from app.plans.models import PlanCreate, ProjectPlan
from app.plans.service import PlanService


@dataclass(frozen=True)
class ContinuationBounds:
    max_steps: int = 10
    max_retries: int = 1
    max_runtime_seconds: float = 120.0


class AutonomousContinuationService:
    """Durable, bounded orchestration over Rexroad's existing PlanService."""

    def __init__(self, plans: PlanService, executor: PlanExecutionCoordinator) -> None:
        self._plans = plans
        self._executor = executor

    def create_goal(self, request: PlanCreate, bounds: ContinuationBounds | None = None) -> ProjectPlan:
        chosen = bounds or ContinuationBounds()
        if chosen.max_steps < 1 or chosen.max_steps > 50 or chosen.max_retries < 0 or chosen.max_retries > 3:
            raise ValueError("invalid continuation bounds")
        metadata = {**request.metadata, "autonomous": True, "bounds": {"max_steps": chosen.max_steps, "max_retries": chosen.max_retries, "max_runtime_seconds": chosen.max_runtime_seconds}, "created_at": datetime.now(UTC).isoformat()}
        return self._plans.create(request.model_copy(update={"metadata": metadata}))

    def inspect(self, plan_id: str, scope: str) -> dict[str, Any]:
        plan = self._require(plan_id, scope)
        steps = []
        for step in plan.steps:
            dependencies = self._dependencies(step.metadata, plan.steps)
            dependency_states = {item.id: item.status for item in plan.steps if item.id in dependencies}
            if step.status == "pending":
                status = "ready" if all(dependency_states.get(dep) == "completed" for dep in dependencies) else "blocked"
            elif step.status == "in_progress":
                status = "running"
            elif step.status == "failed":
                status = "failed"
            elif step.status == "skipped":
                status = "cancelled"
            else:
                status = "completed"
            steps.append({"id": step.id, "title": step.title, "status": status, "dependencies": dependencies, "reference": step.reference, "retry_count": int(step.metadata.get("retry_count", 0))})
        ready = [item["id"] for item in steps if item["status"] == "ready"]
        running = [item["id"] for item in steps if item["status"] == "running"]
        return {"plan_id": plan.id, "scope": scope, "goal": plan.goal, "workspace": plan.workspace, "plan_status": plan.status, "steps": steps, "ready": ready, "next": running[0] if running else (ready[0] if ready else None), "observed_at": datetime.now(UTC).isoformat()}

    def continue_once(self, *, plan_id: str, scope: str, specifications: dict[str, StepExecutionSpec], bounds: ContinuationBounds | None = None, cancel_check: Any = None) -> dict[str, Any]:
        chosen = bounds or ContinuationBounds()
        if chosen.max_steps < 1 or chosen.max_steps > 50:
            raise ValueError("invalid max_steps")
        attempted: list[str] = []; completed: list[str] = []; stopped = "max_steps"
        for _ in range(chosen.max_steps):
            if cancel_check is not None and cancel_check():
                stopped = "cancelled"; break
            state = self.inspect(plan_id, scope)
            if state["plan_status"] != "active": stopped = f"plan_{state['plan_status']}"; break
            ready_id = state["next"]
            if ready_id is None:
                stopped = "blocked_or_complete"; break
            spec = specifications.get(ready_id)
            if spec is None:
                stopped = "awaiting_execution_specification"; break
            tool = self._executor._tools.get(spec.tool_name)
            if tool.high_impact and spec.approval is None:
                stopped = "awaiting_approval"; break
            attempted.append(ready_id)
            try:
                self._executor.execute_once(scope=scope, plan_id=plan_id, step_id=ready_id, tool_name=spec.tool_name, authorization=spec.authorization, approval=spec.approval, arguments=spec.arguments, verification_policy=spec.verification_policy, session_id=spec.session_id, trace_metadata={"autonomous": True})
                completed.append(ready_id)
            except PlanExecutionError:
                stopped = "failed"; break
        final = self._plans.get(plan_id, scope)
        if final is not None and final.status != "active":
            stopped = f"plan_{final.status}"
        return {"plan_id": plan_id, "attempted": attempted, "completed": completed, "stopped_reason": stopped, "plan_status": final.status if final else "missing", "state": self.inspect(plan_id, scope)}

    def cancel(self, plan_id: str, scope: str) -> ProjectPlan | None:
        return self._plans.cancel(plan_id, scope)

    def continue_with_bridge(self, *, plan_id: str, scope: str, bridge: Any, authorizations: dict[str, Any], max_steps: int = 10) -> dict[str, Any]:
        """Advance only tasks whose bridge specs are currently authorized."""
        if max_steps < 1 or max_steps > 50:
            raise ValueError("invalid max_steps")
        attempted: list[str] = []; completed: list[str] = []; waiting: str | None = None
        for _ in range(max_steps):
            state = self.inspect(plan_id, scope)
            if state["plan_status"] != "active": break
            step_id = state["next"]
            if step_id is None: break
            plan = self._require(plan_id, scope)
            step = next(item for item in plan.steps if item.id == step_id)
            spec, execution = bridge.resolve(plan, step, authorizations)
            if execution is None:
                if spec.execution_status == "completed":
                    reference = spec.worker_reference or "specialized workflow completed"
                    if step.status == "pending":
                        self._plans.transition(plan_id, step_id, "in_progress", scope, reference)
                    self._plans.transition(plan_id, step_id, "completed", scope, reference)
                    completed.append(step_id)
                    continue
                if spec.execution_status in {"failed", "cancelled"} and step.status == "pending":
                    reference = spec.worker_reference or f"specialized workflow {spec.execution_status}"
                    self._plans.transition(plan_id, step_id, "in_progress", scope, reference)
                    self._plans.transition(plan_id, step_id, spec.execution_status, scope, reference)
                elif spec.worker_reference and step.status == "pending":
                    self._plans.transition(plan_id, step_id, "in_progress", scope, spec.worker_reference)
                waiting = spec.execution_status
                break
            attempted.append(step_id)
            try:
                self._executor.execute_once(scope=scope, plan_id=plan_id, step_id=step_id, tool_name=execution.tool_name, authorization=execution.authorization, approval=execution.approval, arguments=execution.arguments, verification_policy=execution.verification_policy, session_id=execution.session_id, trace_metadata={"autonomous": True, "expected_evidence": spec.expected_evidence})
                completed.append(step_id)
            except PlanExecutionError:
                break
        final = self._plans.get(plan_id, scope)
        return {"plan_id": plan_id, "attempted": attempted, "completed": completed, "waiting": waiting, "plan_status": final.status if final else "missing", "state": self.inspect(plan_id, scope)}

    def _require(self, plan_id: str, scope: str) -> ProjectPlan:
        plan = self._plans.get(plan_id, scope)
        if plan is None: raise ValueError("autonomous plan not found")
        return plan

    @staticmethod
    def _dependencies(metadata: dict[str, Any], steps: list[Any]) -> list[str]:
        raw = metadata.get("depends_on", metadata.get("dependencies", []))
        dependencies = {item for item in raw if isinstance(item, str)} if isinstance(raw, list) else set()
        positions = metadata.get("depends_on_positions", [])
        if isinstance(positions, list):
            dependencies.update(steps[index].id for index in positions if isinstance(index, int) and 0 <= index < len(steps))
        return sorted(dependencies)
