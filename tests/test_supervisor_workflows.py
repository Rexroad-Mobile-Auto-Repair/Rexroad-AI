import pytest

from app.subagents import SubAgentService
from app.supervisor_workflows import ResearchVerifyWorkflowCreate, SupervisorResearchVerifyWorkflow


@pytest.mark.asyncio
async def test_research_verify_workflow_requires_explicit_review(tmp_path):
    agents = SubAgentService(tmp_path / "state.sqlite3")
    workflows = SupervisorResearchVerifyWorkflow(tmp_path / "state.sqlite3", agents)
    workflow = workflows.create(ResearchVerifyWorkflowCreate(scope="s", instruction="compare the project evidence", parent_session_id="parent", plan_id="plan", step_id="step"))
    started = await workflows.start_research(workflow.workflow_id, "s")
    assert started.workflow.status == "awaiting_review"
    assert started.researcher is not None
    assert started.researcher.worker_profile == "researcher"
    assert started.workflow.researcher_dispatch_id
    with pytest.raises(ValueError):
        await workflows.start_verification(workflow.workflow_id, "s")
    reviewed = workflows.review_research(workflow.workflow_id, "s", "accepted", "supervisor")
    assert reviewed.researcher_review_status == "accepted"
    verified = await workflows.start_verification(workflow.workflow_id, "s")
    assert verified.workflow.status == "awaiting_verifier_review"
    assert verified.verifier is not None
    assert verified.verifier.worker_profile == "verifier"
    assert verified.workflow.verifier_dispatch_id
    assert workflows.get(workflow.workflow_id, "other") is None
    final = workflows.review_verifier(workflow.workflow_id, "s", "accepted", "supervisor")
    assert final.workflow.status == "completed"
    assert final.final_outcome == "verified"
    assert workflows.result(final.workflow).final_outcome == "verified"
    context = workflows.verified_context(workflow.workflow_id, "s")
    assert context.workflow_id == workflow.workflow_id
    with pytest.raises(ValueError):
        workflows.verified_context(workflow.workflow_id, "other")
    assert verified.workflow.parent_session_id == "parent"
    assert verified.workflow.plan_id == "plan"
    assert verified.workflow.step_id == "step"


@pytest.mark.asyncio
async def test_research_review_action_is_scoped_and_stale_safe(tmp_path):
    path = tmp_path / "state.sqlite3"
    agents = SubAgentService(path)
    workflows = SupervisorResearchVerifyWorkflow(path, agents)
    workflow = workflows.create(ResearchVerifyWorkflowCreate(scope="s", instruction="research this"))
    await workflows.start_research(workflow.workflow_id, "s")
    with pytest.raises(ValueError):
        workflows.review_research(workflow.workflow_id, "other", "accepted")
    accepted = workflows.review_research(workflow.workflow_id, "s", "accepted", "supervisor")
    assert accepted.workflow.status == "awaiting_review"
    with pytest.raises(ValueError):
        workflows.review_research(workflow.workflow_id, "s", "accepted", "supervisor")


@pytest.mark.asyncio
async def test_rejected_research_cannot_start_verification_or_cross_scope(tmp_path):
    agents = SubAgentService(tmp_path / "state.sqlite3")
    workflows = SupervisorResearchVerifyWorkflow(tmp_path / "state.sqlite3", agents)
    workflow = workflows.create(ResearchVerifyWorkflowCreate(scope="s", instruction="research this"))
    await workflows.start_research(workflow.workflow_id, "s")
    with pytest.raises(ValueError):
        await workflows.start_verification(workflow.workflow_id, "other")
    current = workflows.get(workflow.workflow_id, "s")
    agents.review(current.researcher_task_id or "", "s", "rejected", "supervisor")
    with pytest.raises(ValueError):
        await workflows.start_verification(workflow.workflow_id, "s")
    assert workflows.get(workflow.workflow_id, "other") is None


@pytest.mark.asyncio
async def test_workflow_persists_and_research_is_idempotent(tmp_path):
    path = tmp_path / "state.sqlite3"
    agents = SubAgentService(path)
    workflows = SupervisorResearchVerifyWorkflow(path, agents)
    workflow = workflows.create(ResearchVerifyWorkflowCreate(scope="s", instruction="find evidence"))
    first = await workflows.start_research(workflow.workflow_id, "s")
    second = await workflows.start_research(workflow.workflow_id, "s")
    assert second.workflow.researcher_task_id == first.workflow.researcher_task_id
    restarted = SupervisorResearchVerifyWorkflow(path, SubAgentService(path))
    persisted = restarted.get(workflow.workflow_id, "s")
    assert persisted is not None
    assert persisted.researcher_task_id == first.workflow.researcher_task_id


@pytest.mark.asyncio
async def test_cancellation_is_scoped_idempotent_and_preserves_worker_history(tmp_path):
    path = tmp_path / "state.sqlite3"
    agents = SubAgentService(path)
    workflows = SupervisorResearchVerifyWorkflow(path, agents)
    workflow = workflows.create(ResearchVerifyWorkflowCreate(scope="s", instruction="research this"))
    started = await workflows.start_research(workflow.workflow_id, "s")
    task_id = started.workflow.researcher_task_id
    cancelled = workflows.cancel(workflow.workflow_id, "s", "supervisor stopped")
    assert cancelled.status == "cancelled"
    assert cancelled.cancellation_reason == "supervisor stopped"
    assert cancelled.cancelled_at is not None
    assert agents.get(task_id)[1] is not None
    with pytest.raises(ValueError):
        await workflows.start_verification(workflow.workflow_id, "s")
    assert workflows.cancel(workflow.workflow_id, "s").status == "cancelled"
    with pytest.raises(ValueError):
        workflows.cancel(workflow.workflow_id, "other")
    restarted = SupervisorResearchVerifyWorkflow(path, SubAgentService(path))
    assert restarted.get(workflow.workflow_id, "s").status == "cancelled"


def test_workflow_listing_is_bounded_scoped_and_filterable(tmp_path):
    workflows = SupervisorResearchVerifyWorkflow(tmp_path / "state.sqlite3", SubAgentService(tmp_path / "state.sqlite3"))
    first = workflows.create(ResearchVerifyWorkflowCreate(scope="s", instruction="research one"))
    workflows.create(ResearchVerifyWorkflowCreate(scope="other", instruction="research two"))
    workflows.cancel(first.workflow_id, "s")
    assert [item.workflow_id for item in workflows.list("s", status="cancelled")] == [first.workflow_id]
    assert workflows.list("s", 1)[0].scope == "s"
    with pytest.raises(ValueError):
        workflows.list("s", 101)
