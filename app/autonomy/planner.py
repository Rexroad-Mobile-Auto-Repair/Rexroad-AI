from __future__ import annotations

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
        if request.workspace:
            map_hint = str(self._navigator.navigate(request.workspace, "map"))[:12000]
        planner_request = ModelRequest(model=model, messages=[ModelMessage(role="user", content=(f"Goal: {request.goal}\nWorkspace: {request.workspace or 'none'}\nLive repository map: {map_hint}\nDecompose into small bounded tasks. Prefer read-only inspection first. Use only worker names direct, researcher, code_analyst, verifier, supervised_coding, mcp, skill. Return JSON only."))])
        output = await self._structured.generate(provider, planner_request, PlannerOutput, name="rexroad_goal_decomposition")
        validated = self._validate(output)
        plan_steps = []
        positions = {task.key: index for index, task in enumerate(validated.tasks)}
        for task in validated.tasks:
            dependencies = [positions[item] for item in task.dependencies]
            recommendation = self._policy.recommend(SupervisorRecommendationRequest(instruction=task.objective, scope=request.scope, workspace=request.workspace, task_kind="research" if task.worker == "researcher" else "code" if task.worker in {"code_analyst", "supervised_coding"} else "verify" if task.worker == "verifier" else None))
            selected_worker = task.worker if task.worker != "direct" else (recommendation.profile or "direct")
            metadata = {"decomposition_key": task.key, "objective": task.objective, "expected_evidence": task.expected_evidence, "worker": selected_worker, "tool_category": task.tool_category, "verification": task.verification, "mutation_required": task.mutation_required, "depends_on_positions": dependencies, "delegation": recommendation.model_dump()}
            plan_steps.append(PlanStepCreate(title=task.title, metadata=metadata))
        plan = self._autonomy.create_goal(PlanCreate(scope=request.scope, workspace=request.workspace, goal=request.goal, steps=plan_steps, metadata={"original_goal": request.goal, "planner": "structured", "decomposition": validated.model_dump()}))
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
