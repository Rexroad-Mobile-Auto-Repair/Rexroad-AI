import pytest

from app.coding_actions import CodingJobActionRequest, SupervisorCodingActionService
from app.coding_jobs import CodingJob, CodingJobAction


class _Jobs:
    def __init__(self):
        self.calls = 0
        self.item = CodingJob(job_id="w", workflow_id="w", scope="s", workspace="ws", objective="x", status="awaiting_analysis", next_action=CodingJobAction(action="start_analysis", allowed=True, reason="start"), observed_at="2025-01-01T00:00:00Z")

    def get(self, workflow_id, scope):
        self.calls += 1
        return self.item


class _Workflows:
    def __init__(self, jobs): self.jobs = jobs; self.calls = 0

    async def start_analysis(self, workflow_id, scope):
        self.calls += 1
        self.jobs.item = self.jobs.item.model_copy(update={"status": "awaiting_analysis_review", "next_action": CodingJobAction(action="review_analysis", allowed=True, reason="review")})
        return type("Result", (), {"analyst_task_id": "task", "analyst_dispatch_id": "dispatch"})()


class _Proposals:
    def get(self, proposal_id, scope): return None


class _Tools: pass


@pytest.mark.asyncio
async def test_action_dispatches_exactly_one_advertised_action() -> None:
    jobs = _Jobs()
    workflows = _Workflows(jobs)
    service = SupervisorCodingActionService(jobs, workflows, _Proposals(), _Tools())
    result = await service.dispatch("w", CodingJobActionRequest(action="start_analysis", scope="s"))
    assert workflows.calls == 1
    assert result.action == "start_analysis"
    assert result.job.next_action.action == "review_analysis"


@pytest.mark.asyncio
async def test_wrong_action_is_rejected_before_service_call() -> None:
    jobs = _Jobs()
    workflows = _Workflows(jobs)
    service = SupervisorCodingActionService(jobs, workflows, _Proposals(), _Tools())
    with pytest.raises(ValueError, match="not currently permitted"):
        await service.dispatch("w", CodingJobActionRequest(action="execute_checks", scope="s"))
    assert workflows.calls == 0


def test_action_request_registry_has_no_dead_prepare_checks_action() -> None:
    actions = set(CodingJobActionRequest.model_json_schema()["properties"]["action"]["enum"])
    assert "prepare_checks" not in actions
    assert "execute_checks" in actions
