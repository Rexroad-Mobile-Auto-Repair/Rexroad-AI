from pathlib import Path

import pytest

from app.policy.workspaces import (
    WorkspaceAccessError,
    WorkspaceRegistry,
)
from app.tools.filesystem import ReadOnlyFilesystem


@pytest.fixture
def filesystem(tmp_path: Path) -> ReadOnlyFilesystem:
    root = tmp_path / "repo"
    root.mkdir()

    (root / "README.md").write_text(
        "Rexroad AI test repository",
        encoding="utf-8",
    )

    app_dir = root / "app"
    app_dir.mkdir()

    (app_dir / "main.py").write_text(
        "SERVICE_NAME = 'rexroad-ai'",
        encoding="utf-8",
    )

    registry = WorkspaceRegistry(
        {
            "repo": root,
        }
    )

    return ReadOnlyFilesystem(registry)


def test_list_directory(filesystem: ReadOnlyFilesystem) -> None:
    assert filesystem.list("repo") == [
        "README.md",
        "app",
    ]


def test_read_file(filesystem: ReadOnlyFilesystem) -> None:
    assert (
        filesystem.read("repo", "README.md")
        == "Rexroad AI test repository"
    )


def test_search_files(filesystem: ReadOnlyFilesystem) -> None:
    assert filesystem.search(
        "repo",
        "rexroad-ai",
    ) == ["app\\main.py"]


def test_read_denies_path_escape(
    filesystem: ReadOnlyFilesystem,
) -> None:
    with pytest.raises(
        WorkspaceAccessError,
        match="escapes approved workspace",
    ):
        filesystem.read(
            "repo",
            "..\\secret.txt",
        )


def test_list_requires_directory(
    filesystem: ReadOnlyFilesystem,
) -> None:
    with pytest.raises(NotADirectoryError):
        filesystem.list(
            "repo",
            "README.md",
        )


def test_read_requires_file(
    filesystem: ReadOnlyFilesystem,
) -> None:
    with pytest.raises(FileNotFoundError):
        filesystem.read(
            "repo",
            "missing.txt",
        )
