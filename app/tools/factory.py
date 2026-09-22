from app.tools.filesystem import ReadOnlyFilesystem
from app.tools.git import ReadOnlyGit
from app.tools.registry import ToolDefinition, ToolRegistry


def build_tool_registry(
    filesystem: ReadOnlyFilesystem,
    git: ReadOnlyGit,
) -> ToolRegistry:
    registry = ToolRegistry()

    registry.register(
        ToolDefinition(
            name="filesystem.list",
            description="List files and directories inside an approved workspace.",
            permission="read",
            handler=filesystem.list,
        )
    )

    registry.register(
        ToolDefinition(
            name="filesystem.read",
            description="Read a text file inside an approved workspace.",
            permission="read",
            handler=filesystem.read,
        )
    )

    registry.register(
        ToolDefinition(
            name="filesystem.search",
            description="Search text files inside an approved workspace.",
            permission="read",
            handler=filesystem.search,
        )
    )

    registry.register(
        ToolDefinition(
            name="git.status",
            description="Read the Git working tree and branch status.",
            permission="read",
            handler=git.status,
        )
    )

    registry.register(
        ToolDefinition(
            name="git.branch",
            description="Read the current Git branch.",
            permission="read",
            handler=git.branch,
        )
    )

    registry.register(
        ToolDefinition(
            name="git.diff",
            description="Read a Git diff without modifying the repository.",
            permission="read",
            handler=git.diff,
        )
    )

    registry.register(
        ToolDefinition(
            name="git.log",
            description="Read recent Git commit history.",
            permission="read",
            handler=git.log,
        )
    )

    registry.register(
        ToolDefinition(
            name="git.show",
            description="Read details about a Git commit or reference.",
            permission="read",
            handler=git.show,
        )
    )

    return registry
