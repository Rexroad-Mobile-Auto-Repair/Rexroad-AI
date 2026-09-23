import subprocess

import pytest

from app.policy.workspaces import WorkspaceRegistry
from app.tools.filesystem import ReadOnlyFilesystem
from app.tools.registry import ToolDefinition, ToolRegistry


def committed_workspace(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "note.txt").write_text("before", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "note.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    return root


def test_exact_patch_is_contained_atomic_and_hashed(tmp_path):
    root = committed_workspace(tmp_path)
    filesystem = ReadOnlyFilesystem(WorkspaceRegistry({"repo": root}))
    result = filesystem.apply_patch("repo", "note.txt", "before", "after")
    assert result["changed"] is True
    assert result["before_hash"] != result["after_hash"]
    assert (root / "note.txt").read_text(encoding="utf-8") == "after"


def test_patch_rejects_traversal_mismatch_oversize_and_dirty_target(tmp_path):
    root = committed_workspace(tmp_path)
    filesystem = ReadOnlyFilesystem(WorkspaceRegistry({"repo": root}))
    with pytest.raises(PermissionError):
        filesystem.apply_patch("repo", "../outside.txt", "before", "x")
    with pytest.raises(ValueError):
        filesystem.apply_patch("repo", "note.txt", "wrong", "x")
    with pytest.raises(ValueError):
        filesystem.apply_patch("repo", "note.txt", "x" * 64001, "x")
    (root / "note.txt").write_text("user change", encoding="utf-8")
    with pytest.raises(ValueError):
        filesystem.apply_patch("repo", "note.txt", "user change", "replacement")


def test_mutation_tool_requires_dedicated_permission_and_is_high_impact(tmp_path):
    root = committed_workspace(tmp_path)
    filesystem = ReadOnlyFilesystem(WorkspaceRegistry({"repo": root}))
    registry = ToolRegistry(tmp_path / "state.sqlite3")
    registry.register(ToolDefinition(name="filesystem.apply_patch", description="patch", permission="filesystem_write", high_impact=True, handler=filesystem.apply_patch))
    authorization = registry.authorize("filesystem.apply_patch", "scope")
    assert authorization.permission == "filesystem_write"
    assert registry.get("filesystem.apply_patch").high_impact is True
    assert registry.validate_authorization(authorization, "filesystem.apply_patch", "scope")
    assert not registry.validate_authorization(authorization, "filesystem.apply_patch", "other")
