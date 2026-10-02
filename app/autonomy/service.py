from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
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
        self._active_plans: set[tuple[str, str]] = set()
        self._active_lock = Lock()

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
                    self._skip_redundant_coding_steps(plan_id, scope, step_id, plan.steps)
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

    def continue_with_team(self, *, plan_id: str, scope: str, bridge: Any, teams: Any, authorizations: dict[str, Any], max_steps: int = 1) -> dict[str, Any]:
        """Create or reuse one durable team, then hand off to normal continuation."""
        team = teams.get_for_plan(plan_id, scope)
        if team is not None and team["status"] == "completed" and self.inspect(plan_id, scope)["ready"]:
            team = None
        if team is None or team["status"] == "active":
            if team is None:
                team = teams.create(plan_id, scope, bridge, authorizations=authorizations)
            if team is not None and team["status"] == "active":
                result = teams.run(team["team_id"], scope, bridge, authorizations, max_steps=max_steps)
                if result["execution"]["attempted"]:
                    return {"team": result["team"], "execution": result["execution"]}
        return {"team": team, "execution": self.continue_with_bridge(plan_id=plan_id, scope=scope, bridge=bridge, authorizations=authorizations, max_steps=max_steps)}

    def continue_routed(self, *, plan_id: str, scope: str, bridge: Any, teams: Any, authorizations: dict[str, Any], max_steps: int = 10) -> dict[str, Any]:
        if max_steps < 1 or max_steps > 50:
            raise ValueError("invalid max_steps")
        key = (plan_id, scope)
        with self._active_lock:
            if key in self._active_plans:
                return {"plan_id": plan_id, "attempted": [], "completed": [], "waiting": "already_running", "state": self.inspect(plan_id, scope)}
            self._active_plans.add(key)
        try:
            plan = self._require(plan_id, scope)
            if plan.status == "active" and plan.metadata.get("read_only") is True and any(s.status == "in_progress" for s in plan.steps):
                self._plans.recover_read_only(plan_id, scope)
                if hasattr(teams, "restore_completed"):
                    teams.restore_completed(plan_id, scope)
            return self._continue_routed(plan_id=plan_id, scope=scope, bridge=bridge, teams=teams, authorizations=authorizations, max_steps=max_steps)
        finally:
            with self._active_lock:
                self._active_plans.discard(key)

    def _continue_routed(self, *, plan_id: str, scope: str, bridge: Any, teams: Any, authorizations: dict[str, Any], max_steps: int = 10) -> dict[str, Any]:
        """Re-evaluate routing after each step or independent team batch."""
        if max_steps < 1 or max_steps > 50:
            raise ValueError("invalid max_steps")
        attempted: list[str] = []
        completed: list[str] = []
        team = teams.get_for_plan(plan_id, scope)
        waiting = None
        for _ in range(max_steps):
            before = self.inspect(plan_id, scope)
            if before["plan_status"] != "active":
                break
            existing_team = teams.get_for_plan(plan_id, scope)
            assigned = {task_id for member in existing_team.get("members", []) for task_id in member["task_ids"]} if existing_team and existing_team["status"] == "active" else set()
            if assigned.intersection(before["ready"]) or self._has_concurrent_read_only_tasks(plan_id=plan_id, scope=scope, bridge=bridge, authorizations=authorizations):
                routed = self.continue_with_team(plan_id=plan_id, scope=scope, bridge=bridge, teams=teams, authorizations=authorizations, max_steps=1)
                team = routed["team"]
                execution = routed["execution"]
            else:
                execution = self.continue_with_bridge(plan_id=plan_id, scope=scope, bridge=bridge, authorizations=authorizations, max_steps=1)
            attempted.extend(execution.get("attempted", []))
            completed.extend(execution.get("completed", []))
            waiting = execution.get("waiting")
            if not execution.get("completed"):
                break
        final = {"plan_id": plan_id, "attempted": attempted, "completed": completed, "waiting": waiting,
                 "plan_status": self.inspect(plan_id, scope)["plan_status"], "state": self.inspect(plan_id, scope)}
        return {"team": team, "execution": final} if team else final

    def _has_concurrent_read_only_tasks(self, *, plan_id: str, scope: str, bridge: Any, authorizations: dict[str, Any]) -> bool:
        state = self.inspect(plan_id, scope)
        if state["plan_status"] != "active":
            return False
        plan = self._require(plan_id, scope)
        candidates = []
        for step_id in state["ready"]:
            step = next(item for item in plan.steps if item.id == step_id)
            # Probe side-effect free first: bridge.resolve() may dispatch workflows for
            # mutation or researcher/verifier steps, which routing must never trigger.
            try:
                planned = bridge.build_spec(plan, step)
            except ValueError:
                continue
            if planned.mutation_required or planned.worker not in {"direct", "code_analyst", "test_analyst", "architecture_analyst", "security_analyst"}:
                continue
            spec, execution = bridge.resolve(plan, step, authorizations)
            if execution is None or spec.mutation_required:
                continue
            if any(self._conflicts(spec, other) for other in candidates):
                continue
            candidates.append(spec)
            if len(candidates) == 2:
                return True
        return False

    def continue_parallel(self, *, plan_id: str, scope: str, bridge: Any, authorizations: dict[str, Any], max_steps: int = 10, max_concurrency: int = 2, cancel_check: Any = None, allowed_step_ids: set[str] | None = None) -> dict[str, Any]:
        """Run a bounded batch of independent authorized read-only tasks."""
        if max_steps < 1 or max_steps > 50 or max_concurrency < 1 or max_concurrency > 4:
            raise ValueError("invalid parallel continuation bounds")
        attempted: list[str] = []; completed: list[str] = []; failed: list[str] = []; batches: list[list[str]] = []; results: list[dict[str, Any]] = []
        for _ in range(max_steps):
            if cancel_check is not None and cancel_check():
                break
            state = self.inspect(plan_id, scope)
            if state["plan_status"] != "active" or not state["ready"]:
                break
            plan = self._require(plan_id, scope)
            candidates = []
            for step_id in state["ready"]:
                if allowed_step_ids is not None and step_id not in allowed_step_ids:
                    continue
                step = next(item for item in plan.steps if item.id == step_id)
                spec, execution = bridge.resolve(plan, step, authorizations)
                if execution is None or spec.mutation_required or spec.worker not in {"direct", "code_analyst", "test_analyst", "architecture_analyst", "security_analyst"}:
                    continue
                if any(self._conflicts(spec, other[0]) for other in candidates):
                    continue
                candidates.append((spec, execution, step))
                if len(candidates) >= max_concurrency:
                    break
            if not candidates:
                break
            batch_ids = [item[2].id for item in candidates]; batches.append(batch_ids); attempted.extend(batch_ids)
            def run(item: tuple[Any, Any, Any], batch: list[str] = batch_ids) -> dict[str, Any]:
                spec, execution, step = item
                started = datetime.now(UTC)
                try:
                    result = self._executor.execute_once(scope=scope, plan_id=plan_id, step_id=step.id, tool_name=execution.tool_name, authorization=execution.authorization, approval=execution.approval, arguments=execution.arguments, verification_policy=execution.verification_policy, session_id=execution.session_id, trace_metadata={"autonomous": True, "parallel_batch": batch}, allow_parallel=True)
                    return {"step_id": step.id, "worker": spec.worker, "status": "completed", "started_at": started.isoformat(), "ended_at": datetime.now(UTC).isoformat(), "trace_id": result.get("trace_id"), "worker_result": result.get("result") if execution.tool_name == "autonomy.analyze" else None}
                except PlanExecutionError as exc:
                    return {"step_id": step.id, "worker": spec.worker, "status": "failed", "started_at": started.isoformat(), "ended_at": datetime.now(UTC).isoformat(), "failure_reason": str(exc)[:500], "trace_id": exc.trace_id}

            with ThreadPoolExecutor(max_workers=len(candidates), thread_name_prefix="rexroad-worker") as pool:
                for future in as_completed([pool.submit(run, item) for item in candidates]):
                    result = future.result(); results.append(result)
                    (completed if result["status"] == "completed" else failed).append(result["step_id"])
        final = self._plans.get(plan_id, scope)
        positions = {step.id: step.position for step in final.steps} if final else {}
        completed.sort(key=lambda step_id: positions.get(step_id, -1))
        failed.sort(key=lambda step_id: positions.get(step_id, -1))
        results.sort(key=lambda item: positions.get(item["step_id"], -1))
        return {"plan_id": plan_id, "attempted": attempted, "completed": completed, "failed": failed, "batches": batches, "results": results, "plan_status": final.status if final else "missing", "state": self.inspect(plan_id, scope)}

    @staticmethod
    def _conflicts(left: Any, right: Any) -> bool:
        if left.workspace != right.workspace or left.mutation_required or right.mutation_required:
            return True
        return left.tool_name == right.tool_name and left.tool_name not in {"workspace.repo_map", "git.status", "autonomy.analyze"}

    def _skip_redundant_coding_steps(self, plan_id: str, scope: str, completed_step_id: str, steps: list[Any]) -> None:
        completed = next((item for item in steps if item.id == completed_step_id), None)
        if completed is None or completed.metadata.get("worker") != "supervised_coding":
            return
        completed_tokens = self._task_tokens(completed)
        if len(completed_tokens) < 2:
            return
        for candidate in steps:
            if candidate.id == completed_step_id or candidate.status != "pending" or candidate.metadata.get("worker") != "supervised_coding":
                continue
            overlap = len(completed_tokens & self._task_tokens(candidate))
            if overlap >= 2:
                self._plans.transition(plan_id, candidate.id, "skipped", scope, f"redundant with completed task {completed_step_id}")

    @staticmethod
    def _task_tokens(step: Any) -> set[str]:
        text = f"{step.title} {step.metadata.get('objective', '')}".casefold()
        return {token for token in re.findall(r"[a-z0-9]+", text) if len(token) > 3 and token not in {"task", "step", "through", "using", "workflow"}}

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
