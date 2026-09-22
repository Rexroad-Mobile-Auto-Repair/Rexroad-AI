from pathlib import Path

import pytest

from app.agents.models import AgentQueryRequest
from app.agents.service import AgentService
from app.config import Settings
from app.journal.store import ActionJournal
from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse
from app.providers.registry import ProviderRegistry
from app.tools.models import ToolCall
from app.tools.registry import ToolDefinition, ToolRegistry


class ToolCallingProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1

        if self.calls == 1:
            return ModelResponse(
                provider=self.name,
                model=request.model,
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="git.status",
                        arguments={
                            "workspace": "repo",
                        },
                    )
                ],
            )

        return ModelResponse(
            provider=self.name,
            model=request.model,
            content="done",
        )

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_successful_tool_call_is_journaled(
    tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None)

    providers = ProviderRegistry()
    providers.register(ToolCallingProvider())

    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            name="git.status",
            description="Read Git status.",
            permission="read",
            handler=lambda workspace: f"## main ({workspace})",
        )
    )

    journal = ActionJournal(
        tmp_path / "journal.sqlite3"
    )

    service = AgentService(
        settings,
        providers,
        tools=tools,
        journal=journal,
    )

    response = await service.query(
        AgentQueryRequest(
            message="Check status.",
        )
    )

    assert response.content == "done"

    with journal._connect() as connection:
        row = connection.execute(
            """
            SELECT session_id
            FROM action_journal
            LIMIT 1
            """
        ).fetchone()

    assert row is not None

    actions = journal.list_session(
        row["session_id"]
    )

    assert len(actions) == 1
    assert actions[0].tool == "git.status"
    assert actions[0].permission == "read"
    assert actions[0].arguments == {
        "workspace": "repo",
    }
    assert actions[0].status == "success"
    assert actions[0].result_preview == "## main (repo)"


@pytest.mark.asyncio
async def test_failed_tool_call_is_journaled(
    tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None)

    providers = ProviderRegistry()
    providers.register(ToolCallingProvider())

    def failing_handler(workspace: str) -> str:
        raise RuntimeError(
            f"failed for {workspace}"
        )

    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            name="git.status",
            description="Read Git status.",
            permission="read",
            handler=failing_handler,
        )
    )

    journal = ActionJournal(
        tmp_path / "journal.sqlite3"
    )

    service = AgentService(
        settings,
        providers,
        tools=tools,
        journal=journal,
    )

    with pytest.raises(
        RuntimeError,
        match="failed for repo",
    ):
        await service.query(
            AgentQueryRequest(
                message="Check status.",
            )
        )

    with journal._connect() as connection:
        row = connection.execute(
            """
            SELECT session_id
            FROM action_journal
            LIMIT 1
            """
        ).fetchone()

    assert row is not None

    actions = journal.list_session(
        row["session_id"]
    )

    assert len(actions) == 1
    assert actions[0].tool == "git.status"
    assert actions[0].status == "error"
    assert actions[0].error == "failed for repo"
