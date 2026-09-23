from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.agents.models import AgentQueryRequest, AgentQueryResponse
from app.agents.service import AgentService
from app.config import Settings
from app.diagnostics.models import DoctorReport
from app.diagnostics.service import build_local_diagnostics
from app.journal.models import ActionEntry, SessionDetail, SessionSummary
from app.journal.store import ActionJournal
from app.knowledge.cross_workspace import CrossWorkspaceKnowledgeService
from app.knowledge.embeddings import OpenAICompatibleEmbeddingProvider
from app.knowledge.models import KnowledgeIndexResult, KnowledgeSearchResult
from app.knowledge.service import KnowledgeService
from app.knowledge.store import KnowledgeStore
from app.memory.models import MemoryCreate, MemoryRecord, MemoryUpdate
from app.memory.proposals import MemoryProposal, MemoryProposalCreate, ProposalService
from app.memory.service import MemoryService
from app.memory.store import MemoryStore
from app.plans.bridge import TrustedExecutionBridge
from app.plans.execution import PlanExecutionCoordinator, PlanExecutionError
from app.plans.models import PlanCreate, PlanStep, ProjectPlan, StepStatus
from app.plans.service import PlanService
from app.plans.specs import ExecutionSpec, ExecutionSpecService
from app.plans.traces import ExecutionTrace, ExecutionTraceService
from app.policy.factory import build_workspace_registry
from app.policy.workspaces import WorkspaceInfo
from app.project_briefing import ProjectBriefing, ProjectBriefingService
from app.project_history import (
    ProjectChangeBriefing,
    ProjectSnapshot,
    ProjectSnapshotStore,
    ProjectStateComparison,
    ProjectStateHistoryService,
)
from app.project_state import ProjectState, ProjectStateService
from app.providers.factory import build_provider_registry, get_default_model
from app.providers.status import ProviderStatus
from app.subagents import (
    SubAgentContribution,
    SubAgentReview,
    SubAgentService,
    SubAgentTask,
    SubAgentTaskCreate,
)
from app.tools.factory import build_tool_registry
from app.tools.filesystem import ReadOnlyFilesystem
from app.tools.git import ReadOnlyGit
from app.tools.output_policy import sanitize_output
from app.tools.registry import ToolApprovalRequest

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
cross_workspace_service = CrossWorkspaceKnowledgeService(workspace_registry, knowledge_service)
action_journal = ActionJournal(
    settings.action_journal_path
)
memory_service = MemoryService(MemoryStore(settings.action_journal_path))
proposal_service = ProposalService(settings.action_journal_path, memory_service)
plan_service = PlanService(settings.action_journal_path)
tool_registry = build_tool_registry(
    filesystem, git, knowledge_service, memory_service, proposal_service, plan_service,
    settings.action_journal_path, cross_workspace=cross_workspace_service,
)
execution_spec_service = ExecutionSpecService(settings.action_journal_path, plan_service, tool_registry)
execution_bridge = TrustedExecutionBridge(execution_spec_service, tool_registry)
plan_execution = PlanExecutionCoordinator(plan_service, tool_registry, action_journal, lambda workspace, scope, reason: project_history_service.capture_after_success(workspace, scope, reason))
execution_trace_service = ExecutionTraceService(action_journal)
project_state_service = ProjectStateService(workspace_registry, git, memory_service, plan_service, execution_trace_service)
project_briefing_service = ProjectBriefingService(project_state_service, provider_registry, settings)
project_history_service = ProjectStateHistoryService(project_state_service, ProjectSnapshotStore(settings.action_journal_path))
sub_agent_service = SubAgentService(settings.action_journal_path, provider_registry, tool_registry)

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


@app.post("/memory-proposals", response_model=MemoryProposal)
async def create_memory_proposal(request: MemoryProposalCreate) -> MemoryProposal:
    return proposal_service.create(request)


@app.get("/memory-proposals", response_model=list[MemoryProposal])
async def list_memory_proposals(scope: str, status: str = "pending", limit: int = 50) -> list[MemoryProposal]:
    return proposal_service.list(scope, status, limit)  # type: ignore[arg-type]


@app.get("/memory-proposals/{proposal_id}", response_model=MemoryProposal)
async def get_memory_proposal(proposal_id: str, scope: str) -> MemoryProposal:
    proposal = proposal_service.get(proposal_id, scope)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Memory proposal not found")
    return proposal


@app.post("/memory-proposals/{proposal_id}/approve", response_model=MemoryProposal)
async def approve_memory_proposal(proposal_id: str, scope: str) -> MemoryProposal:
    proposal = proposal_service.approve(proposal_id, scope)
    if proposal is None:
        raise HTTPException(status_code=409, detail="Memory proposal cannot be approved")
    return proposal


@app.post("/memory-proposals/{proposal_id}/reject", response_model=MemoryProposal)
async def reject_memory_proposal(proposal_id: str, scope: str) -> MemoryProposal:
    proposal = proposal_service.reject(proposal_id, scope)
    if proposal is None:
        raise HTTPException(status_code=409, detail="Memory proposal cannot be rejected")
    return proposal


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


@app.get("/knowledge/search-across-workspaces")
async def search_across_workspaces(workspaces: list[str], query: str, limit: int = 20, mode: str = "lexical") -> list[KnowledgeSearchResult]:
    return cross_workspace_service.search_across_workspaces(workspaces, query, limit, mode)


@app.post("/agent/query")
async def agent_query(
    request: AgentQueryRequest,
) -> AgentQueryResponse:
    return await agent_service.query(request)


@app.get("/tool-approvals/{request_id}", response_model=ToolApprovalRequest)
async def get_tool_approval(request_id: str, scope: str) -> ToolApprovalRequest:
    try:
        return tool_registry.get_approval_request(request_id, scope)
    except KeyError:
        raise HTTPException(status_code=404, detail="Approval request not found")


@app.post("/tool-approvals/{request_id}/approve", response_model=ToolApprovalRequest)
async def approve_tool_request(request_id: str, scope: str) -> ToolApprovalRequest:
    try:
        return tool_registry.review_approval(request_id, scope, True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Approval request not found") from exc


@app.post("/tool-approvals/{request_id}/reject", response_model=ToolApprovalRequest)
async def reject_tool_request(request_id: str, scope: str) -> ToolApprovalRequest:
    try:
        return tool_registry.review_approval(request_id, scope, False)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Approval request not found") from exc


@app.post("/plans", response_model=ProjectPlan)
async def create_plan(request: PlanCreate) -> ProjectPlan:
    return plan_service.create(request)


class ExecutionSpecCreateRequest(BaseModel):
    scope: str
    plan_id: str
    step_id: str
    tool_name: str
    arguments: dict
    verification: dict
    session_id: str | None = None


@app.post("/execution-specs", response_model=ExecutionSpec)
async def create_execution_spec(request: ExecutionSpecCreateRequest) -> ExecutionSpec:
    try:
        return execution_spec_service.create(**request.model_dump())
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid execution specification") from exc


@app.get("/execution-specs", response_model=list[ExecutionSpec])
async def list_execution_specs(scope: str, status: str | None = None, limit: int = 50) -> list[ExecutionSpec]:
    try:
        specs = execution_spec_service.list(scope, limit)
        return [spec for spec in specs if status is None or spec.status == status]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid execution spec query") from exc


@app.get("/execution-specs/{spec_id}", response_model=ExecutionSpec)
async def get_execution_spec(spec_id: str, scope: str) -> ExecutionSpec:
    spec = execution_spec_service.get(spec_id, scope)
    if spec is None:
        raise HTTPException(status_code=404, detail="Execution spec not found")
    return spec


@app.post("/execution-specs/{spec_id}/ready", response_model=ExecutionSpec)
async def ready_execution_spec(spec_id: str, scope: str) -> ExecutionSpec:
    try:
        return execution_spec_service.mark_ready(spec_id, scope)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Execution spec not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Execution spec is not ready") from exc


@app.post("/execution-specs/{spec_id}/invalidate", response_model=ExecutionSpec)
async def invalidate_execution_spec(spec_id: str, scope: str) -> ExecutionSpec:
    try:
        return execution_spec_service.invalidate(spec_id, scope)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Execution spec not found") from exc


@app.post("/execution-specs/{spec_id}/execute")
async def execute_execution_spec(spec_id: str, scope: str, approval_request_id: str | None = None) -> dict:
    try:
        runtime, _, _ = execution_bridge.prepare(scope=scope, spec_id=spec_id, approval_request_id=approval_request_id)
        persisted = execution_spec_service.get(spec_id, scope)
        if persisted is None:
            raise PlanExecutionError("execution spec not found")
        result = plan_execution.execute_once(scope=scope, plan_id=persisted.plan_id, step_id=persisted.step_id, tool_name=runtime.tool_name, authorization=runtime.authorization, approval=runtime.approval, arguments=runtime.arguments, verification_policy=runtime.verification_policy, session_id=runtime.session_id)
        result["result"] = sanitize_output(result["result"])
        return result
    except (PlanExecutionError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="execution spec cannot be executed") from exc


@app.get("/execution-traces/{trace_id}", response_model=ExecutionTrace)
async def get_execution_trace(trace_id: str, scope: str) -> ExecutionTrace:
    trace = execution_trace_service.get(trace_id, scope)
    if trace is None:
        raise HTTPException(status_code=404, detail="Execution trace not found")
    return trace


@app.get("/project-state", response_model=ProjectState)
async def get_project_state(workspace: str, scope: str) -> ProjectState:
    try:
        return project_state_service.get(workspace, scope)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail="Project state not found") from exc


@app.post("/sub-agent-tasks", response_model=SubAgentTask)
async def create_sub_agent_task(request: SubAgentTaskCreate) -> SubAgentTask:
    try:
        return sub_agent_service.create(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid sub-agent task") from exc


@app.get("/sub-agent-tasks/{task_id}")
async def get_sub_agent_task(task_id: str) -> dict[str, object]:
    record = sub_agent_service.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task, result = record
    return {"task": task, "result": result}


@app.post("/sub-agent-tasks/{task_id}/run")
async def run_sub_agent_task(task_id: str) -> dict[str, object]:
    try:
        return {"result": await sub_agent_service.run(task_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@app.get("/sub-agent-tasks/{task_id}/review", response_model=SubAgentReview)
async def get_sub_agent_review(task_id: str, scope: str) -> SubAgentReview:
    review = sub_agent_service.get_review(task_id, scope)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    return review


@app.post("/sub-agent-tasks/{task_id}/accept", response_model=SubAgentReview)
async def accept_sub_agent_result(task_id: str, scope: str, reviewer_session_id: str | None = None, note: str | None = None) -> SubAgentReview:
    try:
        return sub_agent_service.review(task_id, scope, "accepted", reviewer_session_id, note)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Review unavailable") from exc


@app.post("/sub-agent-tasks/{task_id}/reject", response_model=SubAgentReview)
async def reject_sub_agent_result(task_id: str, scope: str, reviewer_session_id: str | None = None, note: str | None = None) -> SubAgentReview:
    try:
        return sub_agent_service.review(task_id, scope, "rejected", reviewer_session_id, note)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Review unavailable") from exc


@app.get("/sub-agent-tasks/{task_id}/contribution", response_model=SubAgentContribution)
async def get_sub_agent_contribution(task_id: str, scope: str) -> SubAgentContribution:
    try:
        return sub_agent_service.contribution(task_id, scope)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Contribution unavailable") from exc


@app.get("/project-briefing", response_model=ProjectBriefing)
async def get_project_briefing(workspace: str, scope: str, provider: str | None = None) -> ProjectBriefing:
    try:
        return await project_briefing_service.generate(workspace, scope, provider)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail="Project briefing not found") from exc


@app.post("/project-state/snapshots", response_model=ProjectSnapshot)
async def create_project_snapshot(workspace: str, scope: str) -> ProjectSnapshot:
    return project_history_service.snapshot(workspace, scope)


@app.get("/project-state/snapshots", response_model=list[ProjectSnapshot])
async def list_project_snapshots(workspace: str, scope: str, limit: int = 20) -> list[ProjectSnapshot]:
    return project_history_service.list(workspace, scope, limit)


@app.get("/project-state/snapshots/{snapshot_id}", response_model=ProjectSnapshot)
async def get_project_snapshot(snapshot_id: str, workspace: str, scope: str) -> ProjectSnapshot:
    snapshot = project_history_service.get(snapshot_id, workspace, scope)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return snapshot


@app.get("/project-state/compare", response_model=ProjectStateComparison)
async def compare_project_snapshots(from_id: str, to_id: str, workspace: str, scope: str) -> ProjectStateComparison:
    left = project_history_service.get(from_id, workspace, scope)
    right = project_history_service.get(to_id, workspace, scope)
    if left is None or right is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return project_history_service.compare(left, right)


@app.get("/project-state/change-briefing", response_model=ProjectChangeBriefing)
async def project_change_briefing(workspace: str, scope: str, from_id: str, to_id: str) -> ProjectChangeBriefing:
    left = project_history_service.get(from_id, workspace, scope)
    right = project_history_service.get(to_id, workspace, scope)
    if left is None or right is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return project_history_service.change_briefing(project_history_service.compare(left, right), workspace, scope)


@app.get("/plans", response_model=list[ProjectPlan])
async def list_plans(scope: str, limit: int = 50, status: str | None = None) -> list[ProjectPlan]:
    plans = plan_service.list(scope, limit)
    return [plan for plan in plans if status is None or plan.status == status]


@app.get("/plans/{plan_id}/next-step", response_model=PlanStep | None)
async def next_plan_step(plan_id: str, scope: str) -> PlanStep | None:
    if plan_service.get(plan_id, scope) is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan_service.next_step(plan_id, scope)


@app.get("/plans/{plan_id}", response_model=ProjectPlan)
async def get_plan(plan_id: str, scope: str) -> ProjectPlan:
    plan = plan_service.get(plan_id, scope)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


@app.patch("/plans/{plan_id}/steps/{step_id}", response_model=ProjectPlan)
async def update_plan_step(plan_id: str, step_id: str, scope: str, status: StepStatus, reference: str | None = None) -> ProjectPlan:
    try:
        plan = plan_service.transition(plan_id, step_id, status, scope, reference)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Invalid plan step transition") from exc
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


@app.post("/plans/{plan_id}/cancel", response_model=ProjectPlan)
async def cancel_plan(plan_id: str, scope: str) -> ProjectPlan:
    try:
        plan = plan_service.cancel(plan_id, scope)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Plan cannot be cancelled") from exc
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


