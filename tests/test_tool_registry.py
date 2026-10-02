from pathlib import Path

import pytest

from app.policy.workspaces import WorkspaceRegistry
from app.tools.factory import build_tool_registry
from app.tools.filesystem import ReadOnlyFilesystem
from app.tools.git import ReadOnlyGit
from app.tools.registry import (
    ToolDefinition,
    ToolNotRegisteredError,
    ToolRegistry,
)


def test_register_and_get_tool() -> None:
    registry = ToolRegistry()

    def handler() -> str:
        return "ok"

    tool = ToolDefinition(
        name="test.read",
        description="Test tool",
        permission="read",
        handler=handler,
    )

    registry.register(tool)

    assert registry.get("test.read") is tool


def test_tool_names_are_sorted() -> None:
    registry = ToolRegistry()

    registry.register(
        ToolDefinition(
            name="zeta.read",
            description="Zeta",
            permission="read",
            handler=lambda: None,
        )
    )

    registry.register(
        ToolDefinition(
            name="alpha.read",
            description="Alpha",
            permission="read",
            handler=lambda: None,
        )
    )

    assert registry.names() == [
        "alpha.read",
        "zeta.read",
    ]


def test_duplicate_tool_is_rejected() -> None:
    registry = ToolRegistry()

    tool = ToolDefinition(
        name="test.read",
        description="Test",
        permission="read",
        handler=lambda: None,
    )

    registry.register(tool)

    with pytest.raises(
        ValueError,
        match="Tool already registered",
    ):
        registry.register(tool)


def test_unknown_tool_is_rejected() -> None:
    registry = ToolRegistry()

    with pytest.raises(ToolNotRegisteredError):
        registry.get("missing.tool")


def test_execute_registered_tool() -> None:
    registry = ToolRegistry()

    def handler(value: str) -> str:
        return f"read:{value}"

    registry.register(
        ToolDefinition(
            name="test.read",
            description="Test",
            permission="read",
            handler=handler,
        )
    )

    assert registry.execute(
        "test.read",
        value="hello",
    ) == "read:hello"


def test_build_tool_registry_contains_expected_tools(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    workspaces = WorkspaceRegistry(
        {
            "repo": root,
        }
    )

    filesystem = ReadOnlyFilesystem(workspaces)
    git = ReadOnlyGit(workspaces)

    registry = build_tool_registry(
        filesystem,
        git,
    )

    assert registry.names() == [
        "filesystem.apply_patch",
        "filesystem.glob",
        "filesystem.grep",
        "filesystem.list",
        "filesystem.read",
        "filesystem.search",
        "git.branch",
        "git.diff",
        "git.log",
        "git.show",
        "git.status",
            "workspace.run_check",
            "workspace.run_command",
            "workspace.symbols",
        ]

    assert registry.get("filesystem.apply_patch").permission == "filesystem_write"
    assert registry.get("workspace.run_check").permission == "workspace_check"
    assert all(
        tool.permission == "read"
        for tool in registry.definitions()
        if tool.name not in {"filesystem.apply_patch", "workspace.run_check", "workspace.run_command"}
    )
