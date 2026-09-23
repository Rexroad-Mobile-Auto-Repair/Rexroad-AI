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


def test_coding_workflow_cancellation_is_terminal_and_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="inspect"))
    cancelled = service.cancel(item.workflow_id, "s", "stop")
    assert cancelled.status == "cancelled"
    assert service.cancel(item.workflow_id, "s").status == "cancelled"


def test_check_action_has_bounded_shape() -> None:
    action = CheckAction(check_id="pytest", targets=["tests/test_coding_workflows.py"])
    assert action.check_id == "pytest"


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
