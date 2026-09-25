from pathlib import Path

from app.autonomy.bridge import PlannedTaskExecutionBridge
from app.autonomy.service import AutonomousContinuationService
from app.autonomy.team import AutonomousTeamCoordinator
from app.plans.execution import PlanExecutionCoordinator
from app.plans.models import PlanCreate, PlanStepCreate
from app.plans.service import PlanService
from app.tools.registry import ToolDefinition, ToolRegistry


def build(tmp_path: Path):
    plans = PlanService(tmp_path / "plans.sqlite3")
    tools = ToolRegistry()
    tools.register(ToolDefinition(name="workspace.repo_map", description="map", permission="read", handler=lambda **args: {"ok": True}, parameters={"type": "object"}))
    continuation = AutonomousContinuationService(plans, PlanExecutionCoordinator(plans, tools))
    return plans, tools, continuation, PlannedTaskExecutionBridge(plans, continuation), AutonomousTeamCoordinator(tmp_path / "teams.sqlite3", plans, continuation)


def test_team_requires_two_independent_ready_tasks_and_reconstructs(tmp_path: Path) -> None:
    plans, tools, continuation, bridge, teams = build(tmp_path)
    plan = continuation.create_goal(PlanCreate(scope="s", workspace="repo", goal="inspect", steps=[
        PlanStepCreate(title="one", metadata={"worker": "direct", "tool_category": "repo_map"}),
        PlanStepCreate(title="two", metadata={"worker": "code_analyst", "tool_category": "repo_map"}),
        PlanStepCreate(title="later", metadata={"worker": "direct", "tool_category": "repo_map", "depends_on_positions": [0, 1]}),
    ]))
    team = teams.create(plan.id, "s", bridge)
    assert team is not None and len(team["members"]) == 2
    restored = AutonomousTeamCoordinator(tmp_path / "teams.sqlite3", plans, continuation).get(team["team_id"], "s")
    assert restored is not None and restored["plan_id"] == plan.id
    auth = tools.authorize("workspace.repo_map", "s")
    result = teams.run(team["team_id"], "s", bridge, {"workspace.repo_map": auth})
    assert len(result["execution"]["completed"]) == 2
    assert result["team"]["status"] == "completed"


def test_team_not_created_for_one_ready_task(tmp_path: Path) -> None:
    _, _, continuation, bridge, teams = build(tmp_path)
    plan = continuation.create_goal(PlanCreate(scope="s", workspace="repo", goal="inspect", steps=[PlanStepCreate(title="one", metadata={"worker": "direct", "tool_category": "repo_map"})]))
    assert teams.create(plan.id, "s", bridge) is None


def test_continuation_creates_and_reuses_team(tmp_path: Path) -> None:
    _, tools, continuation, bridge, teams = build(tmp_path)
    plan = continuation.create_goal(PlanCreate(scope="s", workspace="repo", goal="inspect", steps=[
        PlanStepCreate(title="one", metadata={"worker": "direct", "tool_category": "repo_map"}),
        PlanStepCreate(title="two", metadata={"worker": "direct", "tool_category": "repo_map"}),
    ]))
    auth = tools.authorize("workspace.repo_map", "s")
    first = continuation.continue_with_team(plan_id=plan.id, scope="s", bridge=bridge, teams=teams, authorizations={"workspace.repo_map": auth})
    team_id = first["team"]["team_id"]
    second = continuation.continue_with_team(plan_id=plan.id, scope="s", bridge=bridge, teams=teams, authorizations={"workspace.repo_map": auth})
    assert second["team"]["team_id"] == team_id
    assert second["execution"]["plan_status"] == "completed"
