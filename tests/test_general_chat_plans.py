import asyncio

import pytest

from app.agents.planned_review import ChatPlannedReview
from app.plans.models import PlanCreate, PlanStepCreate
from tests.test_autonomous_team import build


@pytest.mark.asyncio
async def test_multiple_goals_id_selection_retry_scope_and_cancel(tmp_path):
    plans, _, autonomy, _, teams = build(tmp_path)
    runs = []
    creations = []

    async def create(goal, scope, workspace, readonly):
        creations.append((goal, readonly))
        return autonomy.create_goal(PlanCreate(scope=scope, workspace=workspace, goal=goal, steps=[
            PlanStepCreate(title="Architecture inspection", metadata={"worker": "architecture_analyst", "mutation_required": False}),
        ]))

    adapter = ChatPlannedReview(plans, autonomy, teams, lambda pid, scope: runs.append((pid, scope)), lambda _: None, create)
    first = await adapter.handle('/plan create Review architecture read-only', 'repo', 's')
    await adapter.handle('/plan create Review architecture read-only', 'repo', 's')
    await adapter.handle('/plan create Review security read-only', 'repo', 's')
    saved = plans.list('chat:s')
    assert len(saved) == 2 and len(creations) == 2 and runs == []
    assert all(readonly for _, readonly in creations)
    pid = saved[0].id
    assert pid in first
    assert 'Architecture inspection' in await adapter.handle('/plan status ' + pid, 'repo', 's')
    listing = await adapter.handle('/plan list', 'repo', 's')
    assert all(plan.id in listing for plan in saved)
    await adapter.handle('/plan start ' + pid, 'repo', 's')
    assert runs == [(pid, 'chat:s')]
    other = await adapter.handle('/plan start ' + pid, 'repo', 'other')
    assert 'No saved plan' in other and len(runs) == 1
    bad = await adapter.handle('/plan start broken-id', 'repo', 's')
    assert 'valid saved plan ID' in bad and len(runs) == 1
    await adapter.handle('/plan cancel ' + pid, 'repo', 's')
    assert plans.get(pid, 'chat:s').status == 'cancelled'
    await adapter.handle('/plan continue ' + pid, 'repo', 's')
    assert len(runs) == 1


@pytest.mark.asyncio
async def test_status_is_available_while_another_request_runs(tmp_path):
    plans, _, autonomy, _, teams = build(tmp_path)
    plan = autonomy.create_goal(PlanCreate(scope='chat:s', workspace='repo', goal='inspect', steps=[PlanStepCreate(title='step')]))
    adapter = ChatPlannedReview(plans, autonomy, teams, lambda *_: None, lambda _: None)
    async with adapter._locks.setdefault('s', asyncio.Lock()):
        response = await asyncio.wait_for(adapter.handle('/plan status ' + plan.id, 'repo', 's'), timeout=1)
    assert plan.id in response
