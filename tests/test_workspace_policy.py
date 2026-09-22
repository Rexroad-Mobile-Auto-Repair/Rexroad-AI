from pathlib import Path

import pytest

from app.policy.workspaces import (
    WorkspaceAccessError,
    WorkspaceRegistry,
)


def test_workspace_names_are_sorted(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(
        {
            "zeta": tmp_path / "zeta",
            "alpha": tmp_path / "alpha",
        }
    )

    assert registry.names() == ["alpha", "zeta"]


def test_resolve_path_inside_workspace(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    registry = WorkspaceRegistry(
        {
            "repo": root,
        }
    )

    resolved = registry.resolve_path(
        "repo",
        "app/main.py",
    )

    assert resolved == root / "app" / "main.py"


def test_unknown_workspace_is_denied(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(
        {
            "repo": tmp_path / "repo",
        }
    )

    with pytest.raises(
        WorkspaceAccessError,
        match="Unknown workspace",
    ):
        registry.get_root("other")


def test_path_escape_is_denied(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    registry = WorkspaceRegistry(
        {
            "repo": root,
        }
    )

    with pytest.raises(
        WorkspaceAccessError,
        match="escapes approved workspace",
    ):
        registry.resolve_path(
            "repo",
            "..\\secret.txt",
        )
