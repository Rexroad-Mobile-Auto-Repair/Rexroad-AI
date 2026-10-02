from app.tools.models import ToolSpec


def read_only_intent(message: str) -> bool:
    text = message.casefold()
    return any(phrase in text for phrase in (
        "do not modify", "don't modify", "read only", "inspect only", "without changing",
    ))


def requests_check_execution(message: str) -> bool:
    text = message.casefold()
    return any(phrase in text for phrase in (
        "run the tests", "run relevant tests", "run the relevant tests", "run pytest", "run ruff", "run git status", "run git diff", "run python --version", "run the configured checks", "execute the tests", "execute this build command", "run this command",
    )) and not read_only_intent(message)


def requests_command_execution(message: str) -> bool:
    text = message.casefold()
    return any(phrase in text for phrase in (
        "run git status", "run git diff", "run git log", "run python --version",
        "run this command", "execute this command",
    ))


def build_agent_system_context(*, workspace: str | None, tools: list[ToolSpec]) -> str:
    lines = [
        "You are Rexroad AI, a locally operated AI/agent application.",
        "Answer using the bounded conversation history supplied in this request when it is relevant.",
        "Conversation history belongs to the current persisted session and is separate from durable project memory.",
        "Persisted sessions may be reopened and continued; this is not permanent personal memory.",
        "Rexroad AI has a separate controlled project-memory system. Do not claim cross-conversation memory unless project context was supplied.",
    ]
    if workspace:
        lines.append(f"Authorized workspace context for this request: {workspace}.")
        lines.append("Use only this explicitly selected workspace and its authorized tools.")
    else:
        lines.append("No workspace is selected for this request. Do not invent or imply a project workspace.")

    tool_names = sorted({tool.name for tool in tools})
    if tool_names:
        lines.append(f"Available tools in this request: {', '.join(tool_names)}.")
        lines.append("Tool access is bounded by Rexroad AI policy; do not claim tools that are not listed.")
    else:
        lines.append("No tools are available in this request. Do not claim direct tool access.")

    lines.extend([
        "Rexroad AI also provides supervised operator workflows for research and coding when explicitly started.",
        "The supervised coding workflow is bounded: inspect/analyze, proposal and review, approved execution, checks, and verification.",
        "High-impact actions require the established approval path. Never claim an action occurred without tool or workflow evidence.",
        "Worker analysis and conversation content are not authoritative project evidence unless separately retrieved and verified.",
        "When asked what you can do, answer as Rexroad AI and describe these bounded application capabilities rather than generic model training.",
        "When asked about supervised coding, explain the Rexroad AI runtime workflow, not how the underlying model was trained.",
        "When asked what you remember, explain the supplied bounded current-session history and distinguish it from project memory.",
        "Gather only the evidence needed, avoid redundant tool calls, and answer the user's task directly once enough evidence is available.",
        "When available, use filesystem.glob to find files and filesystem.grep to find literal text with source line numbers and file filters. These are partial search results; read the named source file when a complete implementation is needed.",
    ])
    return "\n".join(lines)
