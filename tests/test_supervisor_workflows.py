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
    review = agents.review(started.workflow.researcher_task_id or "", "s", "accepted", "supervisor")
    assert review.status == "accepted"
    verified = await workflows.start_verification(workflow.workflow_id, "s")
    assert verified.workflow.status == "completed"
    assert verified.verifier is not None
    assert verified.verifier.worker_profile == "verifier"
    assert verified.workflow.verifier_dispatch_id
    assert verified.workflow.parent_session_id == "parent"
    assert verified.workflow.plan_id == "plan"
    assert verified.workflow.step_id == "step"


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
