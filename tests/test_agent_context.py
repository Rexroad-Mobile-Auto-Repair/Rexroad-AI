from app.agents.context import build_agent_system_context
from app.tools.models import ToolSpec


def test_context_is_identity_grounded_and_workspace_free_without_selection() -> None:
    context = build_agent_system_context(workspace=None, tools=[])
    assert "You are Rexroad AI" in context
    assert "No workspace is selected" in context
    assert "acceptance_test" not in context
    assert "seo_crawler" not in context
    assert "project-memory" in context
    assert "No tools are available" in context


def test_context_describes_only_runtime_tools_and_selected_workspace() -> None:
    context = build_agent_system_context(
        workspace="acceptance_test",
        tools=[ToolSpec(name="filesystem.read", description="Read files")],
    )
    assert "Authorized workspace context for this request: acceptance_test." in context
    assert "filesystem.read" in context
    assert "knowledge.search" not in context
    assert "supervised coding workflow" in context
    assert "approval path" in context


def test_context_is_bounded_and_does_not_execute_or_persist_anything() -> None:
    tools = [ToolSpec(name=f"tool-{index}", description="tool") for index in range(100)]
    context = build_agent_system_context(workspace=None, tools=tools)
    assert len(context) < 8000
