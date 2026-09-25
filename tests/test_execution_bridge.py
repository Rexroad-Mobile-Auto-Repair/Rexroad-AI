from pathlib import Path

from app.autonomy.bridge import PlannedTaskExecutionBridge
from app.autonomy.dispatch import PlannedWorkerDispatcher
from app.autonomy.service import AutonomousContinuationService
from app.plans.execution import PlanExecutionCoordinator
from app.plans.models import PlanCreate, PlanStepCreate
from app.plans.service import PlanService
from app.tools.registry import ToolDefinition, ToolRegistry


def build(tmp_path: Path):
    plans = PlanService(tmp_path / "plans.sqlite3")
    tools = ToolRegistry()
    tools.register(ToolDefinition(name="workspace.repo_map", description="map", permission="read", handler=lambda **args: {"evidence": args}, parameters={"type": "object"}))
    continuation = AutonomousContinuationService(plans, PlanExecutionCoordinator(plans, tools))
    return plans, tools, PlannedTaskExecutionBridge(plans, continuation), continuation


def test_deterministic_spec_and_dependency_execution(tmp_path: Path) -> None:
    _, tools, bridge, continuation = build(tmp_path)
    plan = continuation.create_goal(PlanCreate(scope="s", workspace="repo", goal="inspect", steps=[PlanStepCreate(title="map", metadata={"worker": "direct", "tool_category": "repo_map", "objective": "map", "expected_evidence": "map", "verification": "present"}), PlanStepCreate(title="later", metadata={"worker": "direct", "tool_category": "repo_map", "depends_on_positions": [0]})]))
    auth = tools.authorize("workspace.repo_map", "s")
    spec, execution = bridge.resolve(plan, plan.steps[0], {"workspace.repo_map": auth})
    assert spec.tool_name == "workspace.repo_map" and execution is not None
    result = continuation.continue_with_bridge(plan_id=plan.id, scope="s", bridge=bridge, authorizations={"workspace.repo_map": auth}, max_steps=2)
    assert result["completed"] == [step.id for step in plan.steps]
    assert result["plan_status"] == "completed"


def test_mutation_waits_without_self_authorization(tmp_path: Path) -> None:
    _, _, bridge, continuation = build(tmp_path)
    plan = continuation.create_goal(PlanCreate(scope="s", workspace="repo", goal="mutate", steps=[PlanStepCreate(title="write", metadata={"worker": "supervised_coding", "tool_category": "supervised_coding", "mutation_required": True})]))
    spec, execution = bridge.resolve(plan, plan.steps[0], {})
    assert spec.execution_status == "waiting_for_approval"
    assert execution is None
    result = continuation.continue_with_bridge(plan_id=plan.id, scope="s", bridge=bridge, authorizations={})
    assert result["waiting"] == "waiting_for_approval"


def test_unknown_worker_fails_safely(tmp_path: Path) -> None:
    _, _, bridge, continuation = build(tmp_path)
    plan = continuation.create_goal(PlanCreate(scope="s", workspace="repo", goal="bad", steps=[PlanStepCreate(title="bad", metadata={"worker": "unknown"})]))
    try:
        bridge.build_spec(plan, plan.steps[0])
    except ValueError as exc:
        assert "worker" in str(exc)
    else:
        raise AssertionError("unknown worker was accepted")


def test_supervised_coding_uses_registered_workflow_adapter(tmp_path: Path) -> None:
    class Coding:
        def create(self, request):
            return type("Workflow", (), {"workflow_id": "wf-1", "status": "awaiting_analysis"})()

    plans = PlanService(tmp_path / "plans.sqlite3")
    continuation = AutonomousContinuationService(plans, PlanExecutionCoordinator(plans, ToolRegistry()))
    dispatcher = PlannedWorkerDispatcher(coding=Coding())
    bridge = PlannedTaskExecutionBridge(plans, continuation, dispatcher)
    plan = continuation.create_goal(PlanCreate(scope="s", workspace="repo", goal="code", steps=[PlanStepCreate(title="code", metadata={"worker": "supervised_coding", "tool_category": "supervised_coding", "mutation_required": True})]))
    spec, execution = bridge.resolve(plan, plan.steps[0], {})
    assert spec.execution_status == "waiting_for_workflow"
    assert execution is None


def test_completed_external_workflow_advances_plan_once(tmp_path: Path) -> None:
    class Workflow:
        workflow_id = "wf-complete"
        status = "completed"
        parent_plan_id = None
        parent_step_id = None

    class Coding:
        def __init__(self):
            self.workflow = None

        def create(self, request):
            self.workflow = Workflow()
            self.workflow.status = "awaiting_analysis"
            self.workflow.parent_plan_id = request.plan_id
            self.workflow.parent_step_id = request.step_id
            return self.workflow

        def list(self, scope, workspace=None, limit=20):
            return [self.workflow] if self.workflow is not None else []

    plans = PlanService(tmp_path / "plans.sqlite3")
    continuation = AutonomousContinuationService(plans, PlanExecutionCoordinator(plans, ToolRegistry()))
    coding = Coding()
    bridge = PlannedTaskExecutionBridge(plans, continuation, PlannedWorkerDispatcher(coding=coding))
    plan = continuation.create_goal(PlanCreate(scope="s", workspace="repo", goal="code", steps=[
        PlanStepCreate(title="code", metadata={"worker": "supervised_coding", "tool_category": "supervised_coding", "mutation_required": True}),
        PlanStepCreate(title="verify", metadata={"worker": "direct", "tool_category": "repo_map", "depends_on_positions": [0]}),
    ]))

    first = continuation.continue_with_bridge(plan_id=plan.id, scope="s", bridge=bridge, authorizations={})
    assert first["waiting"] == "waiting_for_workflow"
    persisted = plans.get(plan.id, "s")
    assert persisted is not None and persisted.steps[0].reference == "wf-complete"
    coding.workflow.status = "completed"
    resumed = continuation.continue_with_bridge(plan_id=plan.id, scope="s", bridge=bridge, authorizations={})
    assert resumed["completed"] == [plan.steps[0].id]
    assert resumed["state"]["next"] == plan.steps[1].id
    repeated = continuation.continue_with_bridge(plan_id=plan.id, scope="s", bridge=bridge, authorizations={})
    assert repeated["completed"] == []


def test_planner_tool_categories_route_specialized_workers(tmp_path: Path) -> None:
    class Research:
        def create(self, request):
            return type("Workflow", (), {"workflow_id": "research-1", "status": "awaiting_review"})()

        def list(self, scope, limit=20):
            return []

    plans = PlanService(tmp_path / "plans.sqlite3")
    continuation = AutonomousContinuationService(plans, PlanExecutionCoordinator(plans, ToolRegistry()))
    bridge = PlannedTaskExecutionBridge(plans, continuation, PlannedWorkerDispatcher(research=Research()))
    plan = continuation.create_goal(PlanCreate(scope="s", workspace="repo", goal="verify", steps=[
        PlanStepCreate(title="verify", metadata={"worker": "verifier", "tool_category": "execute"}),
    ]))
    spec, execution = bridge.resolve(plan, plan.steps[0], {})
    assert spec.execution_status == "waiting_for_worker_dispatch"
    assert execution is None
