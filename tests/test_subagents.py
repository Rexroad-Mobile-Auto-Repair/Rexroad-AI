import pytest

from app.subagents import PROFILES, SubAgentService, SubAgentTaskCreate


def test_profiles_are_narrow_and_task_persists(tmp_path):
    service = SubAgentService(tmp_path / "state.sqlite3")
    task = service.create(SubAgentTaskCreate(worker_profile="researcher", scope="s", instruction="find evidence", allowed_tools=["knowledge.search"]))
    assert set(task.allowed_tools) <= PROFILES["researcher"]
    assert service.get(task.task_id)[0].status == "pending"


def test_invalid_profile_or_broadened_tools_rejected(tmp_path):
    service = SubAgentService(tmp_path / "state.sqlite3")
    with pytest.raises(ValueError):
        service.create(SubAgentTaskCreate(worker_profile="unknown", scope="s", instruction="x"))
    with pytest.raises(ValueError):
        service.create(SubAgentTaskCreate(worker_profile="researcher", scope="s", instruction="x", allowed_tools=["plan_write"]))


@pytest.mark.asyncio
async def test_provider_free_run_is_structured_and_safe(tmp_path):
    service = SubAgentService(tmp_path / "state.sqlite3")
    task = service.create(SubAgentTaskCreate(worker_profile="verifier", scope="s", instruction="check state"))
    result = await service.run(task.task_id)
    assert result.status == "completed"
    assert result.safe_reason == "deterministic_stub"
