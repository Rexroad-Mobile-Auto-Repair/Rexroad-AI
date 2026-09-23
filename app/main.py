from fastapi import FastAPI, HTTPException

from app.agents.models import AgentQueryRequest, AgentQueryResponse
from app.agents.service import AgentService
from app.config import Settings
from app.diagnostics.models import DoctorReport
from app.diagnostics.service import build_local_diagnostics
from app.journal.models import ActionEntry, SessionDetail, SessionSummary
from app.journal.store import ActionJournal
from app.knowledge.embeddings import OpenAICompatibleEmbeddingProvider
from app.knowledge.models import KnowledgeIndexResult, KnowledgeSearchResult
from app.knowledge.service import KnowledgeService
from app.knowledge.store import KnowledgeStore
from app.memory.models import MemoryCreate, MemoryRecord, MemoryUpdate
from app.memory.service import MemoryService
from app.memory.store import MemoryStore
from app.policy.factory import build_workspace_registry
from app.policy.workspaces import WorkspaceInfo
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
knowledge_service = KnowledgeService(
    workspace_registry,
    KnowledgeStore(settings.knowledge_index_path),
    OpenAICompatibleEmbeddingProvider(
        base_url=settings.local_openai_base_url,
        model=settings.local_embedding_model,
        api_key=settings.local_openai_api_key,
        timeout=settings.request_timeout_seconds,
    ),
    max_source_bytes=settings.knowledge_max_source_bytes,
    max_extracted_bytes=settings.knowledge_max_extracted_bytes,
    max_extracted_sections=settings.knowledge_max_extracted_sections,
    max_document_chunks=settings.knowledge_max_document_chunks,
)
tool_registry = build_tool_registry(filesystem, git, knowledge_service)

action_journal = ActionJournal(
    settings.action_journal_path
)
memory_service = MemoryService(MemoryStore(settings.action_journal_path))

agent_service = AgentService(
    settings,
    provider_registry,
    tools=tool_registry,
    journal=action_journal,
)
diagnostics = build_local_diagnostics(settings)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "rexroad-ai",
        "version": "0.1.0",
    }


@app.get("/workspaces")
async def workspaces() -> list[WorkspaceInfo]:
    return workspace_registry.list()


@app.get("/system/doctor")
async def system_doctor() -> DoctorReport:
    return await diagnostics.report()


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


@app.get("/sessions")
async def sessions(
    limit: int = 50,
) -> list[SessionSummary]:
    return action_journal.list_sessions(limit=limit)


@app.get("/sessions/{session_id}")
async def session_detail(
    session_id: str,
) -> SessionDetail:
    summary = action_journal.get_session(session_id)

    if summary is None:
        raise HTTPException(
            status_code=404,
            detail="Session not found",
        )

    return SessionDetail(
        summary=summary,
        actions=action_journal.list_session(session_id),
    )

@app.get("/journal")
async def journal(
    limit: int = 50,
) -> list[ActionEntry]:
    return action_journal.list_recent(limit=limit)


@app.get("/journal/session/{session_id}")
async def journal_session(
    session_id: str,
) -> list[ActionEntry]:
    return action_journal.list_session(session_id)


@app.post("/memories", response_model=MemoryRecord)
async def create_memory(request: MemoryCreate) -> MemoryRecord:
    return memory_service.create(request)


@app.get("/memories", response_model=list[MemoryRecord])
async def list_memories(scope: str, category: str | None = None, status: str = "active", limit: int = 50) -> list[MemoryRecord]:
    return memory_service.list(scope, category, status, limit)


@app.get("/memories/search", response_model=list[MemoryRecord])
async def search_memories(scope: str, query: str, limit: int = 20) -> list[MemoryRecord]:
    return memory_service.search(scope, query, limit)


@app.get("/memories/{memory_id}", response_model=MemoryRecord)
async def get_memory(memory_id: str) -> MemoryRecord:
    record = memory_service.get(memory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return record


@app.patch("/memories/{memory_id}", response_model=MemoryRecord)
async def update_memory(memory_id: str, request: MemoryUpdate) -> MemoryRecord:
    record = memory_service.update(memory_id, request)
    if record is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return record


@app.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str) -> dict[str, bool]:
    if not memory_service.delete(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True}


@app.post("/knowledge/index/{workspace}")
async def index_knowledge(workspace: str) -> KnowledgeIndexResult:
    return knowledge_service.index(workspace)


@app.get("/knowledge/search")
async def search_knowledge(
    workspace: str,
    query: str,
    limit: int = 10,
    mode: str = "lexical",
) -> list[KnowledgeSearchResult]:
    return knowledge_service.search(workspace, query, limit, mode)


@app.post("/agent/query")
async def agent_query(
    request: AgentQueryRequest,
) -> AgentQueryResponse:
    return await agent_service.query(request)


