from pathlib import Path

import pytest

from app.journal.store import ActionJournal
from app.plans.execution import PlanExecutionCoordinator, PlanExecutionError
from app.plans.models import PlanCreate, PlanStepCreate
from app.plans.service import PlanService
from app.tools.registry import ToolDefinition, ToolRegistry


def setup_plan(tmp_path: Path):
    plans = PlanService(tmp_path / "plans.sqlite3")
    plan = plans.create(PlanCreate(scope="a", goal="do work", steps=[PlanStepCreate(title="one"), PlanStepCreate(title="two")]))
    tools = ToolRegistry()
    tools.register(ToolDefinition(name="safe.read", description="safe", permission="read", handler=lambda value: f"ok:{value}"))
    return plans, plan, PlanExecutionCoordinator(plans, tools)


def test_executes_exactly_one_step_and_completes_after_verification(tmp_path: Path) -> None:
    plans, plan, coordinator = setup_plan(tmp_path)
    calls = []
    auth = coordinator._tools.authorize("safe.read", "a")
    result = coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="safe.read", authorization=auth, arguments={"value": "x"}, verify=lambda value: calls.append(value) or value == "ok:x")
    assert result["result"] == "ok:x"
    saved = plans.get(plan.id, "a")
    assert saved.steps[0].status == "completed"
    assert saved.steps[1].status == "pending"
    assert calls == ["ok:x"]


def test_invalid_scope_step_terminal_and_permission_fail_safely(tmp_path: Path) -> None:
    plans, plan, coordinator = setup_plan(tmp_path)
    with pytest.raises(PlanExecutionError):
        coordinator.execute_once(scope="b", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="safe.read", authorization=coordinator._tools.authorize("safe.read", "a"), arguments={"value": "x"})
    with pytest.raises(PlanExecutionError):
        coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[1].id, tool_name="safe.read", authorization=coordinator._tools.authorize("safe.read", "a"), arguments={"value": "x"})
    coordinator._tools.register(ToolDefinition(name="write", description="write", permission="plan_write", handler=lambda: "bad"))
    with pytest.raises(PlanExecutionError):
        coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="write", authorization=coordinator._tools.authorize("safe.read", "a"), arguments={})
    plans.cancel(plan.id, "a")
    with pytest.raises(PlanExecutionError):
        coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="safe.read", authorization=coordinator._tools.authorize("safe.read", "a"), arguments={"value": "x"})


def test_tool_and_verification_failures_mark_step_failed(tmp_path: Path) -> None:
    plans, plan, coordinator = setup_plan(tmp_path)
    coordinator._tools.register(ToolDefinition(name="bad", description="bad", permission="read", handler=lambda: (_ for _ in ()).throw(RuntimeError("secret"))))
    with pytest.raises(PlanExecutionError, match="tool execution failed"):
        coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="bad", authorization=coordinator._tools.authorize("bad", "a"), arguments={})
    assert plans.get(plan.id, "a").steps[0].status == "failed"
    plan2 = plans.create(PlanCreate(scope="a", goal="x", steps=[PlanStepCreate(title="one")]))
    with pytest.raises(PlanExecutionError, match="verification failed"):
        coordinator.execute_once(scope="a", plan_id=plan2.id, step_id=plan2.steps[0].id, tool_name="safe.read", authorization=coordinator._tools.authorize("safe.read", "a"), arguments={"value": "x"}, verify=lambda _: False)


def test_execution_journal_preserves_linkage_and_failure_safety(tmp_path: Path, monkeypatch) -> None:
    plans, plan, coordinator = setup_plan(tmp_path)
    journal = ActionJournal(tmp_path / "journal.sqlite3")
    coordinator = PlanExecutionCoordinator(plans, coordinator._tools, journal)
    result = coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="safe.read", authorization=coordinator._tools.authorize("safe.read", "a", "session-1"), arguments={"value": "x"}, session_id="session-1")
    entry = journal.list_session("session-1")[0]
    assert entry.status == "success"
    assert entry.arguments["plan_id"] == plan.id
    assert entry.arguments["step_id"] == plan.steps[0].id
    assert entry.arguments["trace_id"] == result["trace_id"]
    monkeypatch.setattr(journal, "record", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("secret")))
    second = plans.create(PlanCreate(scope="a", goal="second", steps=[PlanStepCreate(title="one")]))
    with pytest.raises(PlanExecutionError, match="execution journal failed"):
        coordinator.execute_once(scope="a", plan_id=second.id, step_id=second.steps[0].id, tool_name="safe.read", authorization=coordinator._tools.authorize("safe.read", "a", "session-2"), arguments={"value": "x"}, session_id="session-2")
    assert plans.get(second.id, "a").steps[0].status == "failed"


def test_declared_mutation_permission_is_required_and_verified(tmp_path: Path) -> None:
    plans, plan, coordinator = setup_plan(tmp_path)
    calls: list[str] = []
    coordinator._tools.register(ToolDefinition(name="mutate", description="mutate", permission="plan_write", handler=lambda: calls.append("called") or "changed"))
    with pytest.raises(PlanExecutionError, match="permission denied"):
        coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="mutate", authorization=coordinator._tools.authorize("safe.read", "a"), arguments={})
    assert calls == []
    result = coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="mutate", authorization=coordinator._tools.authorize("mutate", "a"), arguments={}, verify=lambda value: value == "changed")
    assert result["result"] == "changed"
    assert plans.get(plan.id, "a").steps[0].status == "completed"


def test_high_impact_tool_requires_bound_one_time_approval(tmp_path: Path) -> None:
    plans, plan, coordinator = setup_plan(tmp_path)
    calls: list[str] = []
    coordinator._tools.register(ToolDefinition(name="danger", description="danger", permission="plan_write", high_impact=True, handler=lambda value: calls.append(value) or "done"))
    auth = coordinator._tools.authorize("danger", "a", "session-1")
    with pytest.raises(PlanExecutionError, match="approval required"):
        coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="danger", authorization=auth, arguments={"value": "x"}, session_id="session-1")
    approval = coordinator._tools.approve(auth, {"value": "x"})
    result = coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="danger", authorization=auth, approval=approval, arguments={"value": "x"}, session_id="session-1", verify=lambda value: value == "done")
    assert result["result"] == "done"
    assert calls == ["x"]
    second = plans.create(PlanCreate(scope="a", goal="again", steps=[PlanStepCreate(title="one")]))
    with pytest.raises(PlanExecutionError, match="invalid tool approval"):
        coordinator.execute_once(scope="a", plan_id=second.id, step_id=second.steps[0].id, tool_name="danger", authorization=auth, approval=approval, arguments={"value": "x"}, session_id="session-1")


def test_verified_success_invokes_snapshot_hook_once_after_completion(tmp_path: Path) -> None:
    plans, plan, base = setup_plan(tmp_path)
    events = []
    coordinator = PlanExecutionCoordinator(plans, base._tools, on_success=lambda *args: events.append(args))
    coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="safe.read", authorization=base._tools.authorize("safe.read", "a"), arguments={"value": "x"}, verify=lambda value: value == "ok:x")
    assert events == [("", "a", "verified_execution")]
    assert plans.get(plan.id, "a").steps[0].status == "completed"


@pytest.mark.parametrize("verify", [lambda _: False, None])
def test_failed_execution_does_not_invoke_snapshot_hook(tmp_path: Path, verify) -> None:
    plans, plan, base = setup_plan(tmp_path)
    events = []
    coordinator = PlanExecutionCoordinator(plans, base._tools, on_success=lambda *args: events.append(args))
    if verify is None:
        base._tools.register(ToolDefinition(name="bad", description="bad", permission="read", handler=lambda: (_ for _ in ()).throw(RuntimeError("x"))))
        name, args = "bad", {}
    else:
        name, args = "safe.read", {"value": "x"}
    with pytest.raises(PlanExecutionError):
        coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name=name, authorization=base._tools.authorize(name, "a"), arguments=args, verify=verify)
    assert events == []


def test_snapshot_hook_failure_does_not_undo_success(tmp_path: Path) -> None:
    plans, plan, base = setup_plan(tmp_path)
    coordinator = PlanExecutionCoordinator(plans, base._tools, on_success=lambda *_: (_ for _ in ()).throw(RuntimeError("snapshot")))
    result = coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="safe.read", authorization=base._tools.authorize("safe.read", "a"), arguments={"value": "x"})
    assert result["result"] == "ok:x"
    assert plans.get(plan.id, "a").steps[0].status == "completed"


def test_hook_receives_workspace_and_scope_and_no_hook_pre_completion(tmp_path: Path) -> None:
    plans, plan, base = setup_plan(tmp_path)
    events = []
    coordinator = PlanExecutionCoordinator(plans, base._tools, on_success=lambda *args: events.append((args, plans.get(plan.id, "a").steps[0].status)))
    coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="safe.read", authorization=base._tools.authorize("safe.read", "a"), arguments={"value": "x"})
    assert events == [(('', "a", "verified_execution"), "completed")]
