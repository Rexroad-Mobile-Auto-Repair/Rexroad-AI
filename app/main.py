from fastapi import FastAPI

from app.agents.models import AgentQueryRequest, AgentQueryResponse
from app.agents.service import AgentService
from app.config import Settings
from app.journal.models import ActionEntry
from app.journal.store import ActionJournal
from app.policy.factory import build_workspace_registry
from app.providers.factory import build_provider_registry, get_default_model
from app.providers.status import ProviderStatus
from app.tools.factory import build_tool_registry
from app.tools.filesystem import ReadOnlyFilesystem
from app.tools.git import ReadOnlyGit

app = FastAPI(
    title="Rexroad AI",
    version="0.1.0",
)

settings = Settings()

provider_registry = build_provider_registry(settings)

workspace_registry = build_workspace_registry(settings)
filesystem = ReadOnlyFilesystem(workspace_registry)
git = ReadOnlyGit(workspace_registry)
tool_registry = build_tool_registry(filesystem, git)

action_journal = ActionJournal(
    settings.action_journal_path
)

agent_service = AgentService(
    settings,
    provider_registry,
    tools=tool_registry,
    journal=action_journal,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "rexroad-ai",
        "version": "0.1.0",
    }


@app.get("/providers")
async def providers() -> list[ProviderStatus]:
    results: list[ProviderStatus] = []

    for name in provider_registry.names():
        provider = provider_registry.get(name)

        results.append(
            ProviderStatus(
                name=name,
                configured=True,
                healthy=await provider.health_check(),
                model=get_default_model(settings, name),
            )
        )

    return results


@app.get("/journal")
async def journal(
    limit: int = 50,
) -> list[ActionEntry]:
    return action_journal.list_recent(limit=limit)


@app.post("/agent/query")
async def agent_query(
    request: AgentQueryRequest,
) -> AgentQueryResponse:
    return await agent_service.query(request)
