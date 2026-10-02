import subprocess
from pathlib import Path

import pytest

from app.coding_workflows import (
    CheckAction,
    CodingWorkflowCreate,
    CodingWorkflowService,
    PatchAction,
)
from app.policy.workspaces import WorkspaceAccessError, WorkspaceRegistry


class _Git:
    def __init__(self, status: str = "## main") -> None:
        self._status = status

    def status(self, workspace: str) -> str:
        return self._status

    def branch(self, workspace: str) -> str:
        return "main"

    def show(self, workspace: str, ref: str) -> str:
        return "abc123"


def _service(tmp_path: Path, status: str = "## main") -> CodingWorkflowService:
    class Agents:
        def get_review(self, task_id: str, scope: str):
            return type("Review", (), {"status": "accepted"})()

    class Plans:
        def create(self, request):
            raise AssertionError("not needed for these tests")

    return CodingWorkflowService(
        tmp_path / "state.db",
        WorkspaceRegistry({"ws": tmp_path}),
        _Git(status),
        Agents(),
        Plans(),
        object(), object(), object(), object(),
    )


def test_coding_workflow_requires_registered_workspace(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(WorkspaceAccessError):
        service.create(CodingWorkflowCreate(scope="s", workspace="missing", instruction="inspect"))


def test_coding_workflow_creation_is_persisted(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="inspect"))
    assert item.status == "awaiting_analysis"
    assert service.get(item.workflow_id, "s") == item
    assert service.get(item.workflow_id, "other") is None


def test_execution_attempt_linkage_is_explicit_and_restart_safe(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="inspect"))
    item = item.model_copy(update={"plan_id": "plan"})
    service._save(item)
    item = service._record_attempt(item, "spec-a", "step-a", "patch")
    service._finish_attempt(item.workflow_id, "s", item.execution_attempts[0].attempt_id, "failed", "trace-a")
    restarted = _service(tmp_path).get(item.workflow_id, "s")
    assert restarted is not None
    assert [(attempt.spec_id, attempt.sequence, attempt.status, attempt.trace_id) for attempt in restarted.execution_attempts] == [("spec-a", 1, "failed", "trace-a")]


def test_patch_action_is_bounded_by_workflow_service(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="inspect"))
    item = item.model_copy(update={"status": "awaiting_analysis_review", "analyst_task_id": "task"})
    service._save(item)
    patches = [PatchAction(relative_path=f"f{i}.txt", expected_text="a", replacement="b") for i in range(6)]
    with pytest.raises(ValueError, match="five"):
        service.prepare(item.workflow_id, "s", patches)


@pytest.mark.parametrize("kind", ["patch", "check"])
def test_next_attempt_preserves_previous_completion_from_stale_snapshot(tmp_path: Path, kind: str) -> None:
    service = _service(tmp_path)
    item = service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="inspect"))
    stale = service._record_attempt(item, "spec-a", "step-a", kind)
    service._finish_attempt(item.workflow_id, "s", stale.execution_attempts[-1].attempt_id, "succeeded", "trace-a")
    service._record_attempt(stale, "spec-b", "step-b", kind)
    restored = _service(tmp_path).get(item.workflow_id, "s")
    assert [(a.spec_id, a.status, a.trace_id) for a in restored.execution_attempts] == [
        ("spec-a", "succeeded", "trace-a"), ("spec-b", "started", None),
    ]


def test_coding_workflow_cancellation_is_terminal_and_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="inspect"))
    cancelled = service.cancel(item.workflow_id, "s", "stop")
    assert cancelled.status == "cancelled"
    assert service.cancel(item.workflow_id, "s").status == "cancelled"


def test_check_action_has_bounded_shape() -> None:
    action = CheckAction(check_id="pytest", targets=["tests/test_coding_workflows.py"])
    assert action.check_id == "pytest"


def test_check_retry_preserves_applied_patches_and_failed_attempt(tmp_path):
    from types import SimpleNamespace
    service = _service(tmp_path)
    item = service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="edit"))
    item = item.model_copy(update={"plan_id": "failed-plan", "mutation_spec_ids": ["patch"], "mutation_trace_ids": ["applied"], "check_spec_ids": ["old-check"]})
    service._save(item)
    item = service._record_attempt(item, "old-check", "old-step", "check")
    service._finish_attempt(item.workflow_id, "s", item.execution_attempts[-1].attempt_id, "failed", "failed-trace")
    failed = service.get(item.workflow_id, "s").model_copy(update={"status": "failed", "outcome": "check_failed"})
    service._save(failed)
    created = []
    class Specs:
        def get(self, *args):
            return SimpleNamespace(tool_name="workspace.run_check", arguments={"check_id": "pytest", "targets": ["test.py"]})
        def create(self, **kwargs):
            created.append(kwargs)
            return SimpleNamespace(id="new-check")
        def create_with_connection(self, connection, **kwargs):
            return self.create(**kwargs)
    class Plans:
        def create(self, request):
            return SimpleNamespace(id="retry-plan", steps=[SimpleNamespace(id="retry-step")])
        def create_with_connection(self, connection, request):
            return self.create(request)
    service.specs, service.plans = Specs(), Plans()
    recovered = service.retry_checks(item.workflow_id, "s")
    assert recovered.plan_id == "retry-plan" and recovered.check_spec_ids == ["new-check"]
    assert recovered.mutation_spec_ids == ["patch"] and recovered.mutation_trace_ids == ["applied"]
    assert recovered.execution_attempts == failed.execution_attempts
    assert [x["tool_name"] for x in created] == ["workspace.run_check"]
    assert _service(tmp_path).get(item.workflow_id, "s") == recovered


def test_two_check_specs_are_prepared_before_either_step_runs(tmp_path):
    from tests.test_coding_guidance_e2e import _git_workspace, _services
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _git_workspace(workspace)
    service = _services(tmp_path, workspace)[2]
    item = service.create(CodingWorkflowCreate(scope="s", workspace="fixture", instruction="check"))
    service._save(item.model_copy(update={"status": "awaiting_checks"}))
    prepared = service.prepare_checks(item.workflow_id, "s", [CheckAction(check_id="pytest"), CheckAction(check_id="git_diff_check")])
    assert len(prepared.check_spec_ids) == 2
    assert [step.status for step in service.plans.get(prepared.plan_id, "s").steps] == ["pending", "pending"]
    assert all(service.specs.get(sid, "s").session_id == f"coding:{item.workflow_id}" for sid in prepared.check_spec_ids)


@pytest.mark.parametrize("status,outcome,traces", [("completed", "verified", ["applied"]), ("failed", "rejected", ["applied"]), ("failed", "check_failed", [])])
def test_check_retry_rejects_other_failures_and_terminal_work(tmp_path, status, outcome, traces):
    service = _service(tmp_path)
    item = service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="edit"))
    service._save(item.model_copy(update={"status": status, "outcome": outcome, "mutation_trace_ids": traces}))
    with pytest.raises(ValueError, match="only failed checks"):
        service.retry_checks(item.workflow_id, "s")


@pytest.mark.parametrize("status,review,traces,allowed", [("failed", "rejected", ["passed-check"], True), ("completed", "rejected", ["passed-check"], False), ("failed", "pending", ["passed-check"], False), ("failed", "rejected", [], False)])
def test_verification_retry_requires_rejected_review_and_saved_checks(tmp_path, status, review, traces, allowed):
    service = _service(tmp_path)
    item = service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="edit"))
    service._save(item.model_copy(update={"status": status, "outcome": "rejected", "verifier_review_status": review, "check_trace_ids": traces, "mutation_trace_ids": ["applied"]}))
    if allowed:
        retried = service.retry_verifier(item.workflow_id, "s", "Verified source contract")
        assert retried.status == "awaiting_verification"
        assert _service(tmp_path).get(item.workflow_id, "s").verification_note == "Verified source contract"
        assert retried.mutation_trace_ids == ["applied"] and retried.check_trace_ids == traces
    else:
        with pytest.raises(ValueError, match="only rejected verification"):
            service.retry_verifier(item.workflow_id, "s")


def test_terminal_workflow_cannot_prepare_checks(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="inspect"))
    service._save(item.model_copy(update={"status": "completed"}))
    with pytest.raises(ValueError, match="checks"):
        service.prepare_checks(item.workflow_id, "s", [CheckAction(check_id="pytest")])


def test_cancelled_workflow_cannot_start_analysis(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="inspect"))
    service.cancel(item.workflow_id, "s")
    with pytest.raises(ValueError, match="analysis"):
        import asyncio
        asyncio.run(service.start_analysis(item.workflow_id, "s"))


def test_temporary_git_workspace_baseline_and_restart(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "target.txt").write_text("before", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "target.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "initial"], check=True)
    service = _service(tmp_path)
    item = service.create(CodingWorkflowCreate(scope="repo", workspace="ws", instruction="edit target"))
    restarted = _service(tmp_path)
    assert restarted.get(item.workflow_id, "repo") == item
    assert restarted.git.status("ws") == "## main"


def test_preexisting_dirty_target_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "target.txt").write_text("dirty", encoding="utf-8")
    service = _service(tmp_path, "## main\n M target.txt")
    item = service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="inspect"))
    item = item.model_copy(update={"status": "awaiting_analysis_review", "analyst_task_id": "task"})
    service._save(item)
    with pytest.raises(ValueError, match="dirty"):
        service.prepare(item.workflow_id, "s", [PatchAction(relative_path="target.txt", expected_text="dirty", replacement="clean")])
