import pytest

from app.subagents import PROFILES, SubAgentService, SubAgentTaskCreate
from app.tools.registry import ToolDefinition, ToolRegistry


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


@pytest.mark.asyncio
async def test_completed_result_requires_explicit_review_and_contribution(tmp_path):
    service = SubAgentService(tmp_path / "state.sqlite3")
    task = service.create(SubAgentTaskCreate(worker_profile="researcher", scope="s", instruction="find", parent_session_id="worker-session", plan_id="p", step_id="st"))
    await service.run(task.task_id)
    assert service.get_review(task.task_id, "s").status == "pending"
    with pytest.raises(ValueError):
        service.contribution(task.task_id, "s")
    accepted = service.review(task.task_id, "s", "accepted", "supervisor", "ok")
    assert accepted.status == "accepted"
    contribution = service.contribution(task.task_id, "s")
    assert contribution.plan_id == "p"
    assert service.review(task.task_id, "s", "accepted", "supervisor").status == "accepted"


@pytest.mark.asyncio
async def test_reject_and_scope_or_self_review_are_safe(tmp_path):
    service = SubAgentService(tmp_path / "state.sqlite3")
    task = service.create(SubAgentTaskCreate(worker_profile="verifier", scope="s", instruction="check", parent_session_id="worker"))
    await service.run(task.task_id)
    with pytest.raises(ValueError):
        service.review(task.task_id, "other", "accepted", "supervisor")
    with pytest.raises(ValueError):
        service.review(task.task_id, "s", "accepted", "worker")
    assert service.review(task.task_id, "s", "rejected", "supervisor").status == "rejected"
    with pytest.raises(ValueError):
        service.contribution(task.task_id, "s")


def test_worker_uses_only_read_profile_tools(tmp_path):
    tools = ToolRegistry()
    calls = []
    tools.register(ToolDefinition(name="knowledge.search", description="read", permission="read", handler=lambda: calls.append(1) or {"token": "hidden", "ok": 1}))
    service = SubAgentService(tmp_path / "state.sqlite3", tools=tools)
    task = service.create(SubAgentTaskCreate(worker_profile="researcher", scope="s", instruction="read", allowed_tools=["knowledge.search"]))
    assert service.use_tool(task.task_id, "knowledge.search", {}) == {"token": "[redacted]", "ok": 1}
    assert calls == [1]
