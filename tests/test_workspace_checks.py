from pathlib import Path

import pytest

from app.policy.workspaces import WorkspaceRegistry
from app.tools.checks import WorkspaceChecks
from app.tools.registry import ToolDefinition, ToolRegistry


def test_pytest_check_is_bounded_and_workspace_scoped(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "test_ok.py").write_text("def test_ok():\n    assert 1 == 1\n", encoding="utf-8")
    checks = WorkspaceChecks(WorkspaceRegistry({"repo": root}))
    result = checks.run_check("repo", "pytest", ["test_ok.py"])
    assert result["status"] == "passed"
    assert result["passed"] is True
    assert result["targets"] == ["test_ok.py"]
    assert result["workspace"] == "repo"


def test_check_rejects_unknown_shell_targets_and_outside_paths(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    checks = WorkspaceChecks(WorkspaceRegistry({"repo": root}))
    with pytest.raises(ValueError):
        checks.run_check("repo", "unknown")
    with pytest.raises(PermissionError):
        checks.run_check("repo", "pytest", ["../outside.py"])
    with pytest.raises(PermissionError):
        checks.run_check("repo", "pytest", ["test.py;whoami"])
    with pytest.raises(ValueError):
        checks.run_check("repo", "ruff", ["test.py"])


def test_check_permission_is_distinct_and_workers_do_not_receive_it(tmp_path: Path):
    checks = WorkspaceChecks(WorkspaceRegistry({"repo": tmp_path}))
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="workspace.run_check", description="check", permission="workspace_check", handler=checks.run_check))
    authorization = registry.authorize("workspace.run_check", "scope")
    assert authorization.permission == "workspace_check"
    assert not registry.validate_authorization(authorization, "workspace.run_check", "other")
