from pathlib import Path

import pytest

from app.coding_workflows import CodingWorkflowCreate, CodingWorkflowService, PatchAction
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


def test_patch_action_is_bounded_by_workflow_service(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="inspect"))
    item = item.model_copy(update={"status": "awaiting_analysis_review", "analyst_task_id": "task"})
    service._save(item)
    patches = [PatchAction(relative_path=f"f{i}.txt", expected_text="a", replacement="b") for i in range(6)]
    with pytest.raises(ValueError, match="five"):
        service.prepare(item.workflow_id, "s", patches)
