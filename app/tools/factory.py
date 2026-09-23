from app.knowledge.service import KnowledgeService
from app.memory.proposals import ProposalService
from app.memory.service import MemoryService
from app.plans.service import PlanService
from app.tools.filesystem import ReadOnlyFilesystem
from app.tools.git import ReadOnlyGit
from app.tools.memory import MemoryTools
from app.tools.plans import PlanTools
from app.tools.registry import ToolDefinition, ToolRegistry


def build_tool_registry(
    filesystem: ReadOnlyFilesystem,
    git: ReadOnlyGit,
    knowledge: KnowledgeService | None = None,
    memories: MemoryService | None = None,
    proposals: ProposalService | None = None,
    plans: PlanService | None = None,
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
                        "mode": {
                            "type": "string",
                            "enum": ["lexical", "semantic", "hybrid"],
                            "default": "lexical",
                        },
                    },
                    "required": ["workspace", "query"],
                    "additionalProperties": False,
                },
            )
        )

    if memories is not None and proposals is not None:
        memory_tools = MemoryTools(memories, proposals)
        registry.register(ToolDefinition(
            name="memory.search", description="Search approved project memory in one explicit scope.",
            permission="read", handler=memory_tools.search,
            parameters={"type": "object", "properties": {
                "scope": {"type": "string"}, "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                "category": {"type": ["string", "null"]}, "status": {"type": "string", "default": "active"},
            }, "required": ["scope", "query"], "additionalProperties": False},
        ))
        registry.register(ToolDefinition(
            name="memory.propose", description="Propose project memory for explicit later human approval.",
            permission="propose", handler=memory_tools.propose,
            parameters={"type": "object", "properties": {
                "scope": {"type": "string"}, "category": {"type": "string"},
                "content": {"type": "string"}, "provenance": {"type": "string"},
                "session_reference": {"type": ["string", "null"]},
                "metadata": {"type": ["object", "null"]},
            }, "required": ["scope", "category", "content"], "additionalProperties": False},
        ))
        registry.register(ToolDefinition(
            name="memory.proposal_status", description="Read one memory proposal within an explicit scope.",
            permission="read", handler=memory_tools.proposal_status,
            parameters={"type": "object", "properties": {
                "scope": {"type": "string"}, "proposal_id": {"type": "string"},
            }, "required": ["scope", "proposal_id"], "additionalProperties": False},
        ))

    if plans is not None:
        plan_tools = PlanTools(plans)
        registry.register(ToolDefinition(
            name="plan.create", description="Create an explicit ordered plan without executing it.", permission="plan_create", handler=plan_tools.create,
            parameters={"type": "object", "properties": {"scope": {"type": "string"}, "goal": {"type": "string"}, "workspace": {"type": ["string", "null"]}, "steps": {"type": "array", "minItems": 1, "maxItems": 50, "items": {"type": "object", "properties": {"title": {"type": "string"}, "metadata": {"type": "object"}}, "required": ["title"], "additionalProperties": False}}}, "required": ["scope", "goal", "steps"], "additionalProperties": False},
        ))
        registry.register(ToolDefinition(
            name="plan.list", description="List persisted plans in one explicit scope.", permission="read", handler=plan_tools.list,
            parameters={"type": "object", "properties": {"scope": {"type": "string"}, "status": {"type": ["string", "null"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}}, "required": ["scope"], "additionalProperties": False},
        ))
        registry.register(ToolDefinition(
            name="plan.get", description="Read one persisted plan and its ordered steps.", permission="read", handler=plan_tools.get,
            parameters={"type": "object", "properties": {"scope": {"type": "string"}, "plan_id": {"type": "string"}}, "required": ["scope", "plan_id"], "additionalProperties": False},
        ))
        registry.register(ToolDefinition(
            name="plan.next_step", description="Read the next actionable step without executing it.", permission="read", handler=plan_tools.next_step,
            parameters={"type": "object", "properties": {"scope": {"type": "string"}, "plan_id": {"type": "string"}}, "required": ["scope", "plan_id"], "additionalProperties": False},
        ))
        registry.register(ToolDefinition(
            name="plan.update_step", description="Explicitly advance one plan step through a valid state transition.", permission="plan_write", handler=plan_tools.update_step,
            parameters={"type": "object", "properties": {"scope": {"type": "string"}, "plan_id": {"type": "string"}, "step_id": {"type": "string"}, "status": {"type": "string", "enum": ["in_progress", "completed", "failed", "skipped"]}, "reference": {"type": ["string", "null"]}}, "required": ["scope", "plan_id", "step_id", "status"], "additionalProperties": False},
        ))

    return registry
