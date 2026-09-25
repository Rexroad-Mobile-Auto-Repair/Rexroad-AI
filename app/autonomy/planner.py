from __future__ import annotations

import json
import re
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from app.autonomy.service import AutonomousContinuationService
from app.navigation.service import WorkspaceNavigator
from app.plans.models import PlanCreate, PlanStepCreate, ProjectPlan
from app.providers.models import ModelMessage, ModelRequest
from app.providers.registry import ProviderRegistry
from app.structured_output import StructuredOutputService
from app.supervisor_policy import SupervisorPolicy, SupervisorRecommendationRequest

WorkerKind = Literal["direct", "researcher", "code_analyst", "verifier", "supervised_coding", "mcp", "skill"]


class GoalRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)
    scope: str = Field(min_length=1, max_length=200)
    workspace: str | None = Field(default=None, max_length=100)


class DecomposedTask(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=1, max_length=1000)
    dependencies: list[str] = Field(default_factory=list, max_length=10)
    expected_evidence: str = Field(min_length=1, max_length=500)
    worker: WorkerKind = "direct"
    tool_category: str = Field(default="read", max_length=80)
    verification: str = Field(min_length=1, max_length=500)
    mutation_required: bool = False


class PlannerOutput(BaseModel):
    tasks: list[DecomposedTask] = Field(min_length=1, max_length=12)


class GoalDecompositionService:
    """Structured planner that validates a graph before handing it to autonomy."""

    ALLOWED_WORKERS: ClassVar[frozenset[str]] = frozenset({"direct", "researcher", "code_analyst", "verifier", "supervised_coding", "mcp", "skill"})

    def __init__(self, providers: ProviderRegistry, navigator: WorkspaceNavigator, autonomy: AutonomousContinuationService, policy: SupervisorPolicy | None = None) -> None:
        self._providers = providers
        self._navigator = navigator
        self._autonomy = autonomy
        self._policy = policy or SupervisorPolicy()
        self._structured = StructuredOutputService()

    async def decompose(self, request: GoalRequest, *, provider_name: str, model: str) -> tuple[PlannerOutput, ProjectPlan]:
        provider = self._providers.get(provider_name)
        map_hint = "No workspace selected."
        evidence: dict[str, object] = {"workspace": request.workspace, "source": "none"}
        if request.workspace:
            evidence = self._navigator.navigate(request.workspace, "map")
            map_hint = json.dumps(evidence, sort_keys=True)[:16000]
        planner_request = ModelRequest(model=model, messages=[ModelMessage(role="user", content=(f"Goal: {request.goal}\nWorkspace: {request.workspace or 'none'}\nGrounded pre-plan repository evidence (live, bounded): {map_hint}\nUse only discovered file paths as established facts. If a target is not listed, describe it as needing location. Keep simple repository changes minimal: normally one inspection task, one coherent supervised_coding task, and only genuinely independent verification. The supervised_coding workflow already owns analysis, proposal, specification, approval, patch, checks, and verifier; do not create autonomous tasks for those internal stages. Do not add a researcher merely to reread local files when this evidence is sufficient. Use only worker names direct, researcher, code_analyst, verifier, supervised_coding, mcp, skill. Return JSON only."))])
        output = await self._structured.generate(provider, planner_request, PlannerOutput, name="rexroad_goal_decomposition")
        validated = self._validate(self._normalize(output))
        plan_steps = []
        positions = {task.key: index for index, task in enumerate(validated.tasks)}
        for task in validated.tasks:
            dependencies = [positions[item] for item in task.dependencies]
            recommendation = self._policy.recommend(SupervisorRecommendationRequest(instruction=task.objective, scope=request.scope, workspace=request.workspace, task_kind="research" if task.worker == "researcher" else "code" if task.worker in {"code_analyst", "supervised_coding"} else "verify" if task.worker == "verifier" else None))
            selected_worker = task.worker if task.worker != "direct" else (recommendation.profile or "direct")
            metadata = {"decomposition_key": task.key, "objective": task.objective, "expected_evidence": task.expected_evidence, "worker": selected_worker, "tool_category": task.tool_category, "verification": task.verification, "mutation_required": task.mutation_required, "depends_on_positions": dependencies, "delegation": recommendation.model_dump()}
            plan_steps.append(PlanStepCreate(title=task.title, metadata=metadata))
        plan = self._autonomy.create_goal(PlanCreate(scope=request.scope, workspace=request.workspace, goal=request.goal, steps=plan_steps, metadata={"original_goal": request.goal, "planner": "structured", "decomposition": validated.model_dump(), "preplan_evidence": self._evidence_reference(evidence)}))
        return validated, plan

    def replan(self, request: GoalRequest, previous: ProjectPlan, output: PlannerOutput) -> ProjectPlan:
        validated = self._validate(output)
        if previous.status == "active":
            raise ValueError("replanning requires a non-active or explicitly blocked plan")
        steps = [PlanStepCreate(title=task.title, metadata={"decomposition_key": task.key, "objective": task.objective, "expected_evidence": task.expected_evidence, "worker": task.worker, "verification": task.verification, "mutation_required": task.mutation_required}) for task in validated.tasks]
        return self._autonomy.create_goal(PlanCreate(scope=request.scope, workspace=request.workspace, goal=request.goal, steps=steps, metadata={"original_goal": request.goal, "replan_of": previous.id, "replan_reason": "bounded evidence-based revision"}))

    def _validate(self, output: PlannerOutput) -> PlannerOutput:
        keys = [task.key for task in output.tasks]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate task keys")
        key_set = set(keys)
        graph = {task.key: task.dependencies for task in output.tasks}
        for task in output.tasks:
            if task.worker not in self.ALLOWED_WORKERS or task.key in task.dependencies or any(dep not in key_set for dep in task.dependencies):
                raise ValueError("invalid task dependency or worker")
        visiting: set[str] = set(); visited: set[str] = set()
        def visit(key: str) -> None:
            if key in visiting: raise ValueError("task dependency cycle")
            if key in visited: return
            visiting.add(key)
            for dependency in graph[key]: visit(dependency)
            visiting.remove(key); visited.add(key)
        for key in keys: visit(key)
        return output

    def _normalize(self, output: PlannerOutput) -> PlannerOutput:
        """Coalesce workflow-internal or semantically duplicate planned work."""
        kept: list[DecomposedTask] = []
        aliases: dict[str, str] = {}
        coding: DecomposedTask | None = None
        for task in output.tasks:
            if task.worker == "supervised_coding":
                if coding is not None and self._similar(coding, task):
                    aliases[task.key] = coding.key
                    continue
                coding = task
            if task.worker == "verifier" and coding is not None and self._internal_workflow_stage(coding, task):
                aliases[task.key] = coding.key
                continue
            kept.append(task)
        normalized = []
        for task in kept:
            deps = list(dict.fromkeys(aliases.get(dep, dep) for dep in task.dependencies if aliases.get(dep, dep) != task.key))
            normalized.append(task.model_copy(update={"dependencies": deps}))
        return PlannerOutput(tasks=normalized)

    @staticmethod
    def _tokens(task: DecomposedTask) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9]+", f"{task.title} {task.objective}".casefold()) if len(token) > 3 and token not in {"task", "step", "workflow", "using", "through"}}

    def _similar(self, left: DecomposedTask, right: DecomposedTask) -> bool:
        return len(self._tokens(left) & self._tokens(right)) >= 2

    def _internal_workflow_stage(self, coding: DecomposedTask, candidate: DecomposedTask) -> bool:
        stage = self._tokens(candidate)
        return bool(stage & {"check", "checks", "verify", "verification", "report", "result"}) and self._similar(coding, candidate)

    @staticmethod
    def _evidence_reference(evidence: dict[str, object]) -> dict[str, object]:
        git = evidence.get("git") if isinstance(evidence, dict) else None
        return {"operation": "map", "workspace": evidence.get("workspace"), "file_count": evidence.get("file_count", 0), "files": [item.get("file") for item in evidence.get("files", [])[:100] if isinstance(item, dict)], "symbols": evidence.get("symbols", [])[:100], "git": git}
