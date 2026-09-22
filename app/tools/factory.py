from app.knowledge.service import KnowledgeService
from app.tools.filesystem import ReadOnlyFilesystem
from app.tools.git import ReadOnlyGit
from app.tools.registry import ToolDefinition, ToolRegistry


def build_tool_registry(
    filesystem: ReadOnlyFilesystem,
    git: ReadOnlyGit,
    knowledge: KnowledgeService | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()

    registry.register(
        ToolDefinition(
            name="filesystem.list",
            description="List files and directories inside an approved workspace.",
            permission="read",
            handler=filesystem.list,
            parameters={
                "type": "object",
                "properties": {
                    "workspace": {
                        "type": "string",
                    },
                    "relative_path": {
                        "type": "string",
                        "default": ".",
                    },
                },
                "required": ["workspace"],
                "additionalProperties": False,
            },
        )
    )

    registry.register(
        ToolDefinition(
            name="filesystem.read",
            description="Read a text file inside an approved workspace.",
            permission="read",
            handler=filesystem.read,
            parameters={
                "type": "object",
                "properties": {
                    "workspace": {
                        "type": "string",
                    },
                    "relative_path": {
                        "type": "string",
                    },
                },
                "required": [
                    "workspace",
                    "relative_path",
                ],
                "additionalProperties": False,
            },
        )
    )

    registry.register(
        ToolDefinition(
            name="filesystem.search",
            description="Search text files inside an approved workspace.",
            permission="read",
            handler=filesystem.search,
            parameters={
                "type": "object",
                "properties": {
                    "workspace": {
                        "type": "string",
                    },
                    "query": {
                        "type": "string",
                    },
                    "relative_path": {
                        "type": "string",
                        "default": ".",
                    },
                },
                "required": [
                    "workspace",
                    "query",
                ],
                "additionalProperties": False,
            },
        )
    )

    registry.register(
        ToolDefinition(
            name="git.status",
            description="Read the Git working tree and branch status.",
            permission="read",
            handler=git.status,
            parameters={
                "type": "object",
                "properties": {
                    "workspace": {
                        "type": "string",
                    },
                },
                "required": ["workspace"],
                "additionalProperties": False,
            },
        )
    )

    registry.register(
        ToolDefinition(
            name="git.branch",
            description="Read the current Git branch.",
            permission="read",
            handler=git.branch,
            parameters={
                "type": "object",
                "properties": {
                    "workspace": {
                        "type": "string",
                    },
                },
                "required": ["workspace"],
                "additionalProperties": False,
            },
        )
    )

    registry.register(
        ToolDefinition(
            name="git.diff",
            description="Read a Git diff without modifying the repository.",
            permission="read",
            handler=git.diff,
            parameters={
                "type": "object",
                "properties": {
                    "workspace": {
                        "type": "string",
                    },
                    "ref": {
                        "type": ["string", "null"],
                    },
                },
                "required": ["workspace"],
                "additionalProperties": False,
            },
        )
    )

    registry.register(
        ToolDefinition(
            name="git.log",
            description="Read recent Git commit history.",
            permission="read",
            handler=git.log,
            parameters={
                "type": "object",
                "properties": {
                    "workspace": {
                        "type": "string",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 10,
                    },
                },
                "required": ["workspace"],
                "additionalProperties": False,
            },
        )
    )

    registry.register(
        ToolDefinition(
            name="git.show",
            description="Read details about a Git commit or reference.",
            permission="read",
            handler=git.show,
            parameters={
                "type": "object",
                "properties": {
                    "workspace": {
                        "type": "string",
                    },
                    "ref": {
                        "type": "string",
                        "default": "HEAD",
                    },
                },
                "required": ["workspace"],
                "additionalProperties": False,
            },
        )
    )

    if knowledge is not None:
        registry.register(
            ToolDefinition(
                name="knowledge.search",
                description="Search indexed source code in an approved workspace.",
                permission="read",
                handler=knowledge.search,
                parameters={
                    "type": "object",
                    "properties": {
                        "workspace": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                            "default": 10,
                        },
                    },
                    "required": ["workspace", "query"],
                    "additionalProperties": False,
                },
            )
        )

    return registry
