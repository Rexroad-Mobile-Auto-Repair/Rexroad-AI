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
@pytest.mark.parametrize("profile", ["researcher", "test_analyst", "architecture_analyst", "security_analyst"])
async def test_workflow_loop_confines_workspace_and_persists_result(tmp_path, workspace, expected, profile):
    class Provider:
        name = "openai_compatible"
        count = 0

        async def generate(self, request):
            self.count += 1
            if self.count > 1:
                assert request.tools == []
            return ModelResponse(provider=self.name, model=request.model, content="Grounded answer" if self.count > 1 else "",
                                 tool_calls=[] if self.count > 1 else [ToolCall(id="c", name="filesystem.read", arguments={"workspace": workspace, "relative_path": "example.py"})])

    registry = ProviderRegistry()
    registry.register(Provider())
    tools = ToolRegistry()
    reads = []
    tools.register(ToolDefinition(name="filesystem.read", description="read", permission="read", handler=lambda **args: reads.append(args) or "source"))
    service = SubAgentService(tmp_path / "state.sqlite3", registry, tools, WorkerModelRouter(Settings(), registry))
    instruction = "Research source" if profile == "researcher" else f"Inspect code as {profile}: review source"
    request = SupervisorDispatchRequest(worker_profile=profile, scope="s", workspace="repo", instruction=instruction,
                                        mode="provider_loop", max_tool_calls=1, allowed_tools=["filesystem.read"])
    result = await service.dispatch(request, service.authorize_dispatch(request))
    assert result.status == expected
    assert result.worker_profile == profile
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
    assert team["metadata"]["synthesis"]["reconciliation_status"] == "pending"
    assert team["metadata"]["synthesis"]["requires_more_work"] is True
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


@pytest.mark.asyncio
async def test_required_files_are_read_before_worker_can_complete(tmp_path):
    class Provider:
        name = 'openai_compatible'
        count = 0

        async def generate(self, request):
            self.count += 1
            if self.count == 1:
                return ModelResponse(provider=self.name, model=request.model, content='Premature answer')
            if self.count == 2:
                assert 'Required source evidence is missing' in request.messages[-1].content
                return ModelResponse(provider=self.name, model=request.model, tool_calls=[
                    ToolCall(id='first', name='filesystem.read', arguments={'workspace':'repo','relative_path':'example.py'}),
                    ToolCall(id='second', name='filesystem.read', arguments={'workspace':'repo','relative_path':'test_example.py'}),
                ])
            if self.count == 3:
                assert 'SOURCE_TAIL' in request.messages[-1].content
                return ModelResponse(provider=self.name, model=request.model, tool_calls=[ToolCall(id='repeat', name='filesystem.read', arguments={'workspace':'repo','relative_path':'example.py'})])
            assert request.tools == []
            return ModelResponse(provider=self.name, model=request.model, content='Both sources reviewed.' + 'x' * 2100)

    providers = ProviderRegistry()
    provider = Provider()
    providers.register(provider)
    tools = ToolRegistry()
    tools.register(ToolDefinition(name='filesystem.read', description='read', permission='read', handler=lambda **args: 'source' + 'x' * 2500 + 'SOURCE_TAIL'))
    service = SubAgentService(tmp_path / 'state.sqlite3', providers, tools, WorkerModelRouter(Settings(), providers))
    request = SupervisorDispatchRequest(worker_profile='test_analyst', scope='s', workspace='repo', instruction='Inspect code as test_analyst: review both files', required_files=['example.py','test_example.py'], mode='provider_loop', max_tool_calls=5, allowed_tools=['filesystem.read'])
    result = await service.dispatch(request, service.authorize_dispatch(request))
    assert result.status == 'completed'
    assert len(result.summary) > 2000 and result.summary.endswith('x')
    assert {item['path'] for item in service.source_evidence('s', result.task_id)} == set(request.required_files)
    assert provider.count == 4
    assert len(service.audits('s')[0].tool_usage) == 2
