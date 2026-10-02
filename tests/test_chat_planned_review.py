import pytest

from app.agents.models import AgentQueryRequest
from app.agents.planned_review import ChatPlannedReview
from app.autonomy.bridge import PlannedTaskExecutionBridge
from app.autonomy.team import AutonomousTeamCoordinator
from app.plans.models import PlanCreate, PlanStepCreate
from app.plans.service import PlanService
from app.tools.registry import ToolDefinition
from tests.test_autonomous_team import build
from tests.test_chat_tool_policy import PolicyProvider
from tests.test_chat_tool_policy import build as build_agent

REQUEST = ("Create a saved multi-agent plan to review greeting() in example.py and its tests in test_example.py. "
           "Use two independent read-only analysts. Combine their findings. Do not modify files.")


@pytest.mark.asyncio
async def test_chat_review_runs_persists_and_resumes_without_duplicate_work(tmp_path):
    plans, tools, continuation, _, teams = build(tmp_path)
    calls = []

    def analyze(**args):
        calls.append(args)
        return {"task_id": "worker:" + args["step_id"], "worker_type": "code_analyst", "status": "completed",
                "summary": "Source-grounded findings", "files_examined": ["example.py"]}

    tools.register(ToolDefinition(name="autonomy.analyze", description="internal", permission="read", internal=True, handler=analyze))
    bridge = PlannedTaskExecutionBridge(plans, continuation, analysis_tool="autonomy.analyze")

    def resume(plan_id, scope):
        return continuation.continue_routed(plan_id=plan_id, scope=scope, bridge=bridge, teams=teams,
                                           authorizations={"autonomy.analyze": tools.authorize("autonomy.analyze", scope, "autonomy:" + plan_id)})

    async def create(goal, scope, workspace, readonly):
        assert readonly
        return continuation.create_goal(PlanCreate(scope=scope, workspace=workspace, goal=goal,
            steps=[PlanStepCreate(title=role, metadata={"worker": role, "tool_category": "read", "mutation_required": False,
                                                       "objective": "Inspect the requested source contract"})
                   for role in ("code_analyst", "test_analyst")]))

    handler = ChatPlannedReview(plans, continuation, teams, resume, lambda workspace: None, create_plan=create)
    provider = PolicyProvider()
    service = build_agent(tmp_path, provider, tools)
    service._planned_review_handler = handler.handle
    events = [event async for event in service.query_stream(AgentQueryRequest(message=REQUEST, workspace="repo"))]
    session = events[0]["session_id"]
    assert events[-1]["type"] == "completed"
    assert "Current status: completed" in events[-1]["response"]
    assert len(calls) == 2 and provider.requests == []
    scope = "chat:" + session
    plan = plans.list(scope)[0]
    team_id = teams.get_for_plan(plan.id, scope)["team_id"]
    assert all(not step.metadata["mutation_required"] for step in plan.steps)
    assert {step.metadata["worker"] for step in plan.steps} == {"code_analyst", "test_analyst"}
    assert all("Create a saved" not in step.metadata["objective"] for step in plan.steps)
    # Reconstruct the adapter and stores, as after a server restart.
    restored_plans = PlanService(tmp_path / "plans.sqlite3")
    restored_teams = AutonomousTeamCoordinator(tmp_path / "teams.sqlite3", restored_plans, continuation)
    service._planned_review_handler = ChatPlannedReview(restored_plans, continuation, restored_teams, resume, lambda _: None).handle
    result = await service.query(AgentQueryRequest(message="Continue from the saved state. Show current status.", session_id=session))
    assert team_id in result.content and "Worker task ID:" in result.content
    assert len(calls) == 2 and len(plans.list(scope)) == 1
    result = await service.query(AgentQueryRequest(message="Show both analysts' saved findings and their source evidence.", session_id=session))
    assert "Combined saved findings:" in result.content and "Source-grounded findings" in result.content
    assert len(calls) == 2 and provider.requests == []
    assert "ordinary" not in result.content
    assert await handler.handle("Show the persisted records", "repo", "different") == (
        "No saved multi-agent review exists in this chat. Earlier chat analysis is not a saved workflow.")
    with pytest.raises(ValueError, match="workspace mismatch"):
        await handler.handle("Show current status", "other", session)


@pytest.mark.asyncio
async def test_status_never_creates_or_advances_plan(tmp_path):
    plans, _, continuation, _, teams = build(tmp_path)
    plan = continuation.create_goal(PlanCreate(scope="chat:s", workspace="repo", goal="read",
        metadata={"chat_review": True}, steps=[PlanStepCreate(title="unfinished")]))
    handler = ChatPlannedReview(plans, continuation, teams, lambda *_: pytest.fail("status executed work"), lambda _: None)
    result = await handler.handle("Show persisted records: plan ID, team ID, current status", "repo", "s")
    assert plan.id in result and "not created" in result and "unfinished — ready" in result
    assert plans.get(plan.id, "chat:s").steps[0].status == "pending"
    assert await handler.handle(REQUEST, None, "empty") == "Select a project workspace before starting a saved review."
    assert plans.list("chat:empty") == []


@pytest.mark.parametrize("message", [
    "Explain greeting() and review tests. Do not modify files.",
    "Do not create a multi-agent plan with read-only analysts for tests.",
    "Show persisted records. Do not repeat analysis or create another plan.",
])
def test_ordinary_analysis_and_mutation_requests_do_not_start_review(message):
    assert not ChatPlannedReview.requested(message)


def test_failed_parallel_peer_can_finish_but_no_new_work_can_start(tmp_path):
    plans = PlanService(tmp_path / "state.sqlite3")
    plan = plans.create(PlanCreate(scope="s", goal="review", steps=[PlanStepCreate(title=str(i)) for i in range(3)]))
    first, second, pending = plan.steps
    plans.transition(plan.id, first.id, "in_progress", "s")
    plans.transition(plan.id, second.id, "in_progress", "s")
    plans.transition(plan.id, first.id, "failed", "s")
    result = plans.transition(plan.id, second.id, "completed", "s")
    assert result.status == "failed" and result.steps[1].status == "completed"
    with pytest.raises(ValueError, match="terminal plan"):
        plans.transition(plan.id, pending.id, "in_progress", "s")


@pytest.mark.asyncio
async def test_stream_failure_wakes_iterator(tmp_path):
    import asyncio

    provider = PolicyProvider()
    _, tools, _, _, _ = build(tmp_path)
    service = build_agent(tmp_path, provider, tools)

    async def fail(*_):
        raise RuntimeError("worker failed")

    service._planned_review_handler = fail

    async def consume():
        return [event async for event in service.query_stream(AgentQueryRequest(message=REQUEST, workspace="repo"))]

    with pytest.raises(RuntimeError, match="worker failed"):
        await asyncio.wait_for(consume(), timeout=1)
