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
    result = coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="safe.read", arguments={"value": "x"}, verify=lambda value: calls.append(value) or value == "ok:x")
    assert result["result"] == "ok:x"
    saved = plans.get(plan.id, "a")
    assert saved.steps[0].status == "completed"
    assert saved.steps[1].status == "pending"
    assert calls == ["ok:x"]


def test_invalid_scope_step_terminal_and_permission_fail_safely(tmp_path: Path) -> None:
    plans, plan, coordinator = setup_plan(tmp_path)
    with pytest.raises(PlanExecutionError):
        coordinator.execute_once(scope="b", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="safe.read", arguments={"value": "x"})
    with pytest.raises(PlanExecutionError):
        coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[1].id, tool_name="safe.read", arguments={"value": "x"})
    coordinator._tools.register(ToolDefinition(name="write", description="write", permission="plan_write", handler=lambda: "bad"))
    with pytest.raises(PlanExecutionError):
        coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="write", arguments={})
    plans.cancel(plan.id, "a")
    with pytest.raises(PlanExecutionError):
        coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="safe.read", arguments={"value": "x"})


def test_tool_and_verification_failures_mark_step_failed(tmp_path: Path) -> None:
    plans, plan, coordinator = setup_plan(tmp_path)
    coordinator._tools.register(ToolDefinition(name="bad", description="bad", permission="read", handler=lambda: (_ for _ in ()).throw(RuntimeError("secret"))))
    with pytest.raises(PlanExecutionError, match="tool execution failed"):
        coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="bad", arguments={})
    assert plans.get(plan.id, "a").steps[0].status == "failed"
    plan2 = plans.create(PlanCreate(scope="a", goal="x", steps=[PlanStepCreate(title="one")]))
    with pytest.raises(PlanExecutionError, match="verification failed"):
        coordinator.execute_once(scope="a", plan_id=plan2.id, step_id=plan2.steps[0].id, tool_name="safe.read", arguments={"value": "x"}, verify=lambda _: False)


def test_execution_journal_preserves_linkage_and_failure_safety(tmp_path: Path, monkeypatch) -> None:
    plans, plan, coordinator = setup_plan(tmp_path)
    journal = ActionJournal(tmp_path / "journal.sqlite3")
    coordinator = PlanExecutionCoordinator(plans, coordinator._tools, journal)
    result = coordinator.execute_once(scope="a", plan_id=plan.id, step_id=plan.steps[0].id, tool_name="safe.read", arguments={"value": "x"}, session_id="session-1")
    entry = journal.list_session("session-1")[0]
    assert entry.status == "success"
    assert entry.arguments["plan_id"] == plan.id
    assert entry.arguments["step_id"] == plan.steps[0].id
    assert entry.arguments["trace_id"] == result["trace_id"]
    monkeypatch.setattr(journal, "record", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("secret")))
    second = plans.create(PlanCreate(scope="a", goal="second", steps=[PlanStepCreate(title="one")]))
    with pytest.raises(PlanExecutionError, match="execution journal failed"):
        coordinator.execute_once(scope="a", plan_id=second.id, step_id=second.steps[0].id, tool_name="safe.read", arguments={"value": "x"}, session_id="session-2")
    assert plans.get(second.id, "a").steps[0].status == "failed"
