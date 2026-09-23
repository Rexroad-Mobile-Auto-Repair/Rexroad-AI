import pytest

from app.providers.models import ModelResponse
from app.subagents import PROFILES, SubAgentService, SubAgentTaskCreate, SupervisorDispatchRequest
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


@pytest.mark.asyncio
async def test_explicit_incorporation_is_scoped_idempotent_and_revocable(tmp_path):
    service = SubAgentService(tmp_path / "state.sqlite3")
    task = service.create(SubAgentTaskCreate(worker_profile="researcher", scope="s", instruction="find", parent_session_id="worker"))
    await service.run(task.task_id)
    service.review(task.task_id, "s", "accepted", "supervisor")
    item = service.incorporate(task.task_id, "s", "research", "r1", reviewer_session_id="supervisor")
    assert service.incorporate(task.task_id, "s", "research", "r1").incorporation_id == item.incorporation_id
    assert service.incorporated_contribution(item.incorporation_id, "s").task_id == task.task_id
    revoked = service.revoke_incorporation(item.incorporation_id, "s")
    assert revoked.status == "revoked"
    with pytest.raises(ValueError):
        service.incorporated_contribution(item.incorporation_id, "s")
    assert service.get_incorporation(item.incorporation_id, "s").status == "revoked"


def test_worker_uses_only_read_profile_tools(tmp_path):
    tools = ToolRegistry()
    calls = []
    tools.register(ToolDefinition(name="knowledge.search", description="read", permission="read", handler=lambda: calls.append(1) or {"token": "hidden", "ok": 1}))
    service = SubAgentService(tmp_path / "state.sqlite3", tools=tools)
    task = service.create(SubAgentTaskCreate(worker_profile="researcher", scope="s", instruction="read", allowed_tools=["knowledge.search"]))
    assert service.use_tool(task.task_id, "knowledge.search", {}) == {"token": "[redacted]", "ok": 1}
    assert calls == [1]


@pytest.mark.asyncio
async def test_dispatch_requires_matching_one_time_authorization(tmp_path):
    service = SubAgentService(tmp_path / "state.sqlite3")
    request = SupervisorDispatchRequest(worker_profile="researcher", scope="s", instruction="research this", parent_session_id="parent")
    authorization = service.authorize_dispatch(request)
    result = await service.dispatch(request, authorization)
    assert result.status == "completed"
    with pytest.raises(ValueError):
        await service.dispatch(request, authorization)
    audit = service.audits("s")[0]
    assert audit.task_id == result.task_id
    assert audit.recommended_profile == "researcher"
    assert audit.instruction_fingerprint == authorization.fingerprint
    assert authorization.token not in audit.model_dump_json()
    service.record_tool_usage(audit.dispatch_id, "s", "knowledge.search", "read", "success", {"token": "secret", "ok": True})
    refreshed = service.audit(audit.dispatch_id, "s")
    assert refreshed.tool_usage[0]["sequence"] == 1
    assert "secret" not in refreshed.model_dump_json()


def test_dispatch_rejects_wrong_profile_scope_and_instruction(tmp_path):
    service = SubAgentService(tmp_path / "state.sqlite3")
    request = SupervisorDispatchRequest(worker_profile="researcher", scope="s", instruction="research this")
    with pytest.raises(ValueError):
        service.authorize_dispatch(request.model_copy(update={"worker_profile": "verifier"}))
    authorization = service.authorize_dispatch(request)
    with pytest.raises(ValueError):
        import asyncio
        asyncio.run(service.dispatch(request.model_copy(update={"scope": "other"}), authorization))


@pytest.mark.asyncio
async def test_bounded_provider_tool_loop_sanitizes_and_records_usage(tmp_path):
    class Provider:
        name = "openai_compatible"
        def __init__(self): self.round = 0
        async def generate(self, request):
            self.round += 1
            if self.round == 1:
                from app.tools.models import ToolCall
                return ModelResponse(provider="openai_compatible", model="m", tool_calls=[ToolCall(id="c1", name="knowledge.search", arguments={})])
            return ModelResponse(provider="openai_compatible", model="m", content="done")
        async def health_check(self): return True
    from app.providers.registry import ProviderRegistry
    providers = ProviderRegistry(); providers.register(Provider())
    tools = ToolRegistry(); tools.register(ToolDefinition(name="knowledge.search", description="x", permission="read", handler=lambda: {"token": "secret", "ok": 1}))
    service = SubAgentService(tmp_path / "state.sqlite3", providers, tools)
    request = SupervisorDispatchRequest(worker_profile="researcher", scope="s", instruction="research", allowed_tools=["knowledge.search"])
    auth = service.authorize_dispatch(request)
    task = service.create(SubAgentTaskCreate(**request.model_dump()))
    with __import__("sqlite3").connect(tmp_path / "state.sqlite3") as db:
        db.execute("INSERT INTO supervisor_dispatch_audits (dispatch_id, task_id, scope, recommendation_category, recommended_profile, authorized_profile, instruction_fingerprint, status, created_at, tool_usage_json) VALUES ('d', ?, 's', 'research', 'researcher', 'researcher', ?, 'started', '2026-01-01T00:00:00+00:00', '[]')", (task.task_id, auth.fingerprint))
    result = await service.run_with_tools(task.task_id, "d", "openai_compatible", "m")
    assert result.status == "completed"
    assert service.audit("d", "s").tool_usage[0]["status"] == "success"
    assert "secret" not in service.audit("d", "s").model_dump_json()


def test_dispatch_mode_and_budget_are_bound(tmp_path):
    service = SubAgentService(tmp_path / "state.sqlite3")
    request = SupervisorDispatchRequest(worker_profile="researcher", scope="s", instruction="research", mode="provider_loop", max_tool_calls=1)
    auth = service.authorize_dispatch(request)
    assert auth.fingerprint == service.authorize_dispatch(request).fingerprint
    with pytest.raises(ValueError):
        SupervisorDispatchRequest(worker_profile="researcher", scope="s", instruction="research", mode="provider_loop", max_tool_calls=6)
