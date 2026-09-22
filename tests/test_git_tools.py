from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app.policy.workspaces import WorkspaceRegistry
from app.tools.git import GitCommandError, ReadOnlyGit


@pytest.fixture
def git_tools(tmp_path: Path) -> ReadOnlyGit:
    root = tmp_path / "repo"
    root.mkdir()

    registry = WorkspaceRegistry(
        {
            "repo": root,
        }
    )

    return ReadOnlyGit(registry)


@patch("app.tools.git.subprocess.run")
def test_status_uses_read_only_git_status(
    run_mock: Mock,
    git_tools: ReadOnlyGit,
) -> None:
    run_mock.return_value = Mock(
        returncode=0,
        stdout="## main\n",
        stderr="",
    )

    result = git_tools.status("repo")

    assert result == "## main"
    run_mock.assert_called_once()

    command = run_mock.call_args.args[0]

    assert command == [
        "git",
        "status",
        "--short",
        "--branch",
    ]


@patch("app.tools.git.subprocess.run")
def test_branch_returns_current_branch(
    run_mock: Mock,
    git_tools: ReadOnlyGit,
) -> None:
    run_mock.return_value = Mock(
        returncode=0,
        stdout="main\n",
        stderr="",
    )

    assert git_tools.branch("repo") == "main"


@patch("app.tools.git.subprocess.run")
def test_log_enforces_limit(
    run_mock: Mock,
    git_tools: ReadOnlyGit,
) -> None:
    with pytest.raises(
        ValueError,
        match="between 1 and 100",
    ):
        git_tools.log(
            "repo",
            limit=0,
        )

    run_mock.assert_not_called()


@patch("app.tools.git.subprocess.run")
def test_git_failure_raises(
    run_mock: Mock,
    git_tools: ReadOnlyGit,
) -> None:
    run_mock.return_value = Mock(
        returncode=128,
        stdout="",
        stderr="fatal: not a git repository",
    )

    with pytest.raises(
        GitCommandError,
        match="not a git repository",
    ):
        git_tools.status("repo")
