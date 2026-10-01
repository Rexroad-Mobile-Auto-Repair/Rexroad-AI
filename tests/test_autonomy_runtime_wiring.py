import pytest

from app.autonomy.bridge import TaskExecutionSpec
from app.autonomy.dispatch import PlannedWorkerDispatcher
from app.config import Settings
from app.journal.store import ActionJournal
from app.plans.execution import PlanExecutionCoordinator
from app.plans.models import PlanCreate, PlanStepCreate
from app.providers.models import ModelResponse
from app.providers.registry import ProviderRegistry
from app.subagents import SubAgentService, SupervisorDispatchRequest
from app.tools.models import ToolCall
from app.tools.registry import ToolDefinition, ToolRegistry
from app.worker_routing import WorkerModelRouter
from tests.test_autonomous_team import build


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace,expected", [("repo", "completed"), ("other", "failed")])
async def test_workflow_loop_confines_workspace_and_persists_result(tmp_path, workspace, expected):
    class Provider:
        name = "openai_compatible"
        count = 0

        async def generate(self, request):
            self.count += 1
            return ModelResponse(provider=self.name, model=request.model, content="Grounded answer" if self.count > 1 else "",
                                 tool_calls=[] if self.count > 1 else [ToolCall(id="c", name="filesystem.read", arguments={"workspace": workspace, "relative_path": "example.py"})])

    registry = ProviderRegistry()
    registry.register(Provider())
    tools = ToolRegistry()
    reads = []
    tools.register(ToolDefinition(name="filesystem.read", description="read", permission="read", handler=lambda **args: reads.append(args) or "source"))
    service = SubAgentService(tmp_path / "state.sqlite3", registry, tools, WorkerModelRouter(Settings(), registry))
    request = SupervisorDispatchRequest(worker_profile="researcher", scope="s", workspace="repo", instruction="Research source",
                                        mode="provider_loop", max_tool_calls=1, allowed_tools=["filesystem.read"])
    result = await service.dispatch(request, service.authorize_dispatch(request))
    assert result.status == expected
    assert service.get(result.task_id)[1].status == expected
    assert service.get(result.task_id)[0].status == expected
    assert len(reads) == (1 if workspace == "repo" else 0)


def test_internal_analysis_is_not_exposed_to_models():
    tools = ToolRegistry()
    tools.register(ToolDefinition(name="internal", description="internal", permission="read", handler=lambda: None, internal=True))
    assert tools.specs() == []


def test_routing_rechecks_after_dependency_and_persists_synthesis(tmp_path):
    _plans, tools, continuation, bridge, teams = build(tmp_path)
    plan = continuation.create_goal(PlanCreate(scope="s", workspace="repo", goal="inspect", steps=[
        PlanStepCreate(title="prerequisite", metadata={"worker": "direct", "tool_category": "repo_map"}),
        PlanStepCreate(title="implementation", metadata={"worker": "code_analyst", "tool_category": "repo_map", "depends_on_positions": [0]}),
        PlanStepCreate(title="tests", metadata={"worker": "code_analyst", "tool_category": "repo_map", "depends_on_positions": [0]}),
    ]))
    result = continuation.continue_routed(plan_id=plan.id, scope="s", bridge=bridge, teams=teams,
                                         authorizations={"workspace.repo_map": tools.authorize("workspace.repo_map", "s")})
    assert result["team"]["status"] == "completed"
    team = teams.get_for_plan(plan.id, "s")
    assert len(team["metadata"]["worker_results"]) == 2
    assert team["metadata"]["synthesis"]["requires_more_work"] is False
    repeated = continuation.continue_routed(plan_id=plan.id, scope="s", bridge=bridge, teams=teams, authorizations={})
    assert repeated["team"]["team_id"] == team["team_id"]
    assert repeated["execution"]["attempted"] == []


@pytest.mark.asyncio
async def test_anonymous_supervisor_is_not_null_parent_self_review(tmp_path):
    service = SubAgentService(tmp_path / "state.sqlite3")
    request = SupervisorDispatchRequest(worker_profile="researcher", scope="s", instruction="Research")
    result = await service.dispatch(request, service.authorize_dispatch(request))
    assert service.review(result.task_id, "s", "rejected").status == "rejected"


def test_team_evidence_keeps_coding_goal_single_line():
    captured = []

    class Coding:
        def create(self, request):
            captured.append(request)
            return type("Workflow", (), {"workflow_id": "w", "status": "awaiting_analysis"})()

    class Teams:
        def get_for_plan(self, plan_id, scope):
            return {"status": "completed", "metadata": {"synthesis": {"findings": ["Implementation\nSource evidence"]}}}

    dispatcher = PlannedWorkerDispatcher(coding=Coding(), teams=Teams())
    dispatcher.dispatch(TaskExecutionSpec(plan_id="p", step_id="st", scope="s", workspace="repo", objective="Document greeting", worker="supervised_coding", tool_category="write", expected_evidence="docstring", verification="review", mutation_required=True))
    assert captured[0].team_evidence == ["Implementation\nSource evidence"]
    PlanCreate(scope="s", workspace="repo", goal=captured[0].instruction, steps=[PlanStepCreate(title="patch")])


def test_continuation_records_retrievable_execution_traces(tmp_path):
    plans, tools, continuation, bridge, teams = build(tmp_path)
    journal = ActionJournal(tmp_path / "journal.sqlite3")
    continuation._executor = PlanExecutionCoordinator(plans, tools, journal)
    plan = continuation.create_goal(PlanCreate(scope="s", workspace="repo", goal="inspect", steps=[
        PlanStepCreate(title="one", metadata={"worker": "code_analyst", "tool_category": "repo_map"}),
        PlanStepCreate(title="two", metadata={"worker": "code_analyst", "tool_category": "repo_map"}),
    ]))
    continuation.continue_routed(plan_id=plan.id, scope="s", bridge=bridge, teams=teams,
                                 authorizations={"workspace.repo_map": tools.authorize("workspace.repo_map", "s", "autonomy:" + plan.id)})
    assert len(journal.list_recent(10)) == 2
    assert all(entry.arguments["trace_id"] for entry in journal.list_recent(10))


def test_proposal_preview_resolves_workflow_id_and_preserves_scope(monkeypatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app import main

    monkeypatch.setattr(main.coding_job_service, "get", lambda workflow, scope: SimpleNamespace(proposal_id="proposal-123") if workflow == "workflow-456" and scope == "s" else None)
    monkeypatch.setattr(main.coding_proposal_service, "get", lambda proposal, scope: SimpleNamespace(proposal_id=proposal) if proposal == "proposal-123" and scope == "s" else None)
    monkeypatch.setattr(main.coding_proposal_service, "preview", lambda proposal, scope: {"proposal_id": proposal})
    with TestClient(main.app) as client:
        response = client.get("/supervisor-coding-workflows/workflow-456/proposal/preview", params={"scope": "s"})
        assert response.status_code == 200
        assert response.json()["proposal_id"] == "proposal-123"
        assert client.get("/supervisor-coding-workflows/workflow-456/proposal/preview", params={"scope": "other"}).status_code == 404
