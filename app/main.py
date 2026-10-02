import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.agents.models import AgentQueryRequest, AgentQueryResponse
from app.agents.planned_review import ChatPlannedReview
from app.agents.service import (
    AgentLoopLimitError,
    AgentNoProgressError,
    AgentService,
    AgentSessionError,
)
from app.autonomy.analysis import PlannedAnalysis
from app.autonomy.bridge import PlannedTaskExecutionBridge
from app.autonomy.dispatch import PlannedWorkerDispatcher
from app.autonomy.planner import GoalDecompositionService, GoalRequest
from app.autonomy.reconciliation import TeamReconciler
from app.autonomy.service import AutonomousContinuationService
from app.autonomy.team import AutonomousTeamCoordinator
from app.coding_actions import (
    CodingJobActionRequest,
    CodingJobActionResult,
    SupervisorCodingActionService,
)
from app.coding_guidance import CodingGuidanceService, CodingJobGuidance
from app.coding_jobs import CodingJob, CodingJobService
from app.coding_proposals import CodingProposal, CodingProposalService, ProposalCreate
from app.coding_workflows import (
    CheckAction,
    CodingWorkflow,
    CodingWorkflowCreate,
    CodingWorkflowService,
    PatchAction,
)
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
from app.mcp.config import load_mcp_server_configs
from app.mcp.service import MCPAdapter
from app.memory.models import MemoryCreate, MemoryRecord, MemoryUpdate
from app.memory.proposals import MemoryProposal, MemoryProposalCreate, ProposalService
from app.memory.service import MemoryService
from app.memory.store import MemoryStore
from app.navigation.service import WorkspaceNavigator
from app.openai_compat import (
    REXROAD_MODEL,
    OpenAIChatRequest,
    completion_response,
    safe_agent_error,
    stream_completion,
    to_agent_request,
    validate_request,
)
from app.plans.bridge import TrustedExecutionBridge
from app.plans.execution import PlanExecutionCoordinator, PlanExecutionError
from app.plans.models import PlanCreate, PlanStep, ProjectPlan, StepStatus
from app.plans.service import PlanService
from app.plans.specs import ExecutionSpec, ExecutionSpecService
from app.plans.traces import ExecutionTrace, ExecutionTraceService
from app.policy.factory import build_workspace_registry
from app.policy.workspaces import WorkspaceAccessError, WorkspaceInfo
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
from app.research_actions import (
    ResearchWorkflowActionRequest,
    ResearchWorkflowActionResult,
    ResearchWorkflowActionService,
)
from app.skills.service import SkillService
from app.subagents import (
    SubAgentContribution,
    SubAgentIncorporation,
    SubAgentResult,
    SubAgentReview,
    SubAgentService,
    SubAgentTask,
    SubAgentTaskCreate,
    SupervisorDispatchAudit,
    SupervisorDispatchRequest,
)
from app.supervisor_dashboard import SupervisorDashboardService, SupervisorProjectDashboard
from app.supervisor_policy import (
    SupervisorPolicy,
    SupervisorRecommendation,
    SupervisorRecommendationRequest,
)
from app.supervisor_workflows import (
    ResearchVerifyWorkflow,
    ResearchVerifyWorkflowCreate,
    ResearchVerifyWorkflowResult,
    SupervisorResearchVerifyWorkflow,
)
from app.tools.code_search import WorkspaceCodeSearch
from app.tools.factory import build_tool_registry
from app.tools.filesystem import ReadOnlyFilesystem
from app.tools.git import ReadOnlyGit
from app.tools.output_policy import sanitize_output
from app.tools.registry import ToolApprovalRequest, ToolDefinition
from app.tools.symbols import PythonSymbols
from app.worker_routing import WorkerModelRouter

app = FastAPI(
    title="Rexroad AI",
    version="0.1.0",
)

OPERATOR_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rexroad AI Operator</title>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif}body{margin:0;background:#10151c;color:#e8eef5}main{max-width:1100px;margin:auto;padding:1rem}header,.toolbar,.card{background:#18212b;border:1px solid #2b3a49;border-radius:10px;padding:1rem;margin-bottom:1rem}h1,h2{margin:.1rem 0 .75rem}label{display:flex;gap:.5rem;align-items:center}select,input,button{font:inherit;border-radius:6px;border:1px solid #52677a;padding:.55rem;background:#101820;color:inherit}button{cursor:pointer;background:#245b87}button:hover{background:#2e73a8}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem}.attention{border-left:4px solid #e0a642;padding:.6rem;margin:.45rem 0;background:#202b35}.blocked_failure{border-color:#d65c5c}.review_required{border-color:#65a9d6}.action_ready{border-color:#63bb83}.muted{color:#a8b5c2}.error{color:#ff9696;min-height:1.4rem}.files{font-family:monospace;white-space:pre-wrap}.warning{border:1px solid #d19a36;padding:.7rem;border-radius:6px}
</style></head><body><main>
<header><h1>Rexroad AI</h1><p class="muted">Explicit supervisor control panel. Every action requires a separate click.</p></header>
<section class="toolbar"><label>Workspace <select id="workspace"></select></label><label>Scope <input id="scope" value="operator" maxlength="200"></label><button id="refresh">Refresh</button><span id="error" class="error"></span></section>
<section class="grid"><div class="card"><h2>Project</h2><div id="project" class="muted">Select a workspace.</div></div><div class="card"><h2>Needs Attention</h2><div id="attention" class="muted">None.</div></div></section>
<section class="card"><h2>Search project</h2><p class="muted">Find source files or matching text in the selected workspace. Search text is literal.</p><label>Text to find <input id="search-text" maxlength="200" aria-label="Text to find" placeholder="Function name or text"></label><label>File filter <input id="search-pattern" maxlength="200" aria-label="File filter" value="**/*" placeholder="For example *.py or *.php"></label><button id="search-submit">Search text</button><button id="find-files">Find files</button><p id="search-status" aria-live="polite"></p><div id="search-results"></div></section>
<section class="card"><h2>New supervised coding task</h2><input id="objective" maxlength="4000" size="60" placeholder="Describe the coding objective"><button id="create">Create coding workflow</button></section>
<section class="card"><h2>Coding Jobs</h2><div id="jobs" class="muted">None.</div></section>
<section class="card"><h2>Recent Activity</h2><div id="activity" class="muted">None.</div></section>
</main></body></html>"""

CHAT_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Rexroad AI Chat</title><style>
:root{color-scheme:dark;font-family:system-ui,sans-serif}*{box-sizing:border-box}body{margin:0;background:#10151c;color:#e8eef5}main{height:100vh;display:grid;grid-template-columns:250px 1fr}.side{padding:1rem;background:#18212b;border-right:1px solid #2b3a49;overflow:auto}.brand{font-size:1.25rem;font-weight:700;margin-bottom:1rem}button,select,textarea{font:inherit;border:1px solid #52677a;border-radius:7px;background:#101820;color:inherit;padding:.6rem}button{cursor:pointer;background:#245b87}button:hover{background:#2e73a8}.side button{width:100%;margin-bottom:.6rem}.side a{color:#9fd0f3;display:block;margin:.6rem 0}.session{padding:.55rem;border-radius:6px;cursor:pointer;margin:.3rem 0}.session:hover{background:#263747}.chat{display:flex;flex-direction:column;min-width:0}.messages{flex:1;overflow:auto;padding:2rem max(1rem,calc((100% - 800px)/2))}.msg{padding:.8rem 1rem;border-radius:10px;margin:.8rem 0;white-space:pre-wrap;overflow-wrap:anywhere}.user{background:#245b87;margin-left:15%}.assistant{background:#1c2833;margin-right:15%}.composer{padding:1rem max(1rem,calc((100% - 800px)/2));border-top:1px solid #2b3a49;display:flex;gap:.6rem}.composer textarea{flex:1;min-height:48px;resize:vertical}.status{color:#a8b5c2;min-height:1.4rem;font-size:.9rem}.context{width:100%;margin-bottom:.6rem}.error{color:#ff9696}@media(max-width:700px){main{grid-template-columns:1fr}.side{height:auto;border-right:0;border-bottom:1px solid #2b3a49}.messages{padding:1rem}.user,.assistant{margin-left:0;margin-right:0}}
</style></head><body><main><aside class="side"><div class="brand">Rexroad AI</div><button id="new">New Chat</button><label class="context">Project context<select id="workspace"><option value="">None</option></select></label><a href="/operator">Advanced / Operator</a><div id="sessions" class="status">Recent conversations</div></aside><section class="chat"><div id="messages" class="messages"><div class="msg assistant">Hello. I’m Rexroad AI. How can I help?</div></div><div id="status" class="status"></div><form id="form" class="composer"><textarea id="input" placeholder="Message Rexroad AI..." aria-label="Message"></textarea><button id="send" type="submit">Send</button></form></section></main><script>
const $=id=>document.getElementById(id),messages=$('messages');let busy=false;function safe(v){return String(v??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));}function add(role,text){const n=document.createElement('div');n.className='msg '+role;n.textContent=text;messages.appendChild(n);messages.scrollTop=messages.scrollHeight;}async function api(url,opt){const r=await fetch(url,opt);let d={};try{d=await r.json()}catch{}if(!r.ok)throw Error(d.detail||'Request failed');return d;}async function load(){try{const [ws,ss]=await Promise.all([api('/workspaces'),api('/sessions?limit=20')]);$('workspace').innerHTML='<option value="">None</option>'+ws.filter(x=>x.available).map(x=>`<option value="${safe(x.name)}">${safe(x.name)}</option>`).join('');$('sessions').innerHTML=ss.length?ss.map(x=>`<div class="session" data-session="${safe(x.session_id)}">${safe(x.session_id.slice(0,8))} · ${safe(x.action_count)} messages</div>`).join(''):'<span class="status">No conversations yet.</span>';document.querySelectorAll('[data-session]').forEach(x=>x.onclick=()=>openSession(x.dataset.session));}catch(e){$('status').textContent=e.message;}}async function openSession(id){try{const d=await api('/sessions/'+encodeURIComponent(id));messages.innerHTML='';d.actions.filter(x=>x.tool==='').forEach(x=>add(x.status==='success'?'assistant':'assistant',x.result_preview||x.error||''));if(!messages.children.length)add('assistant','Conversation history has no displayable messages.');}catch(e){$('status').textContent=e.message;}}$('new').onclick=()=>{messages.innerHTML='<div class="msg assistant">New conversation started. How can I help?</div>';$('input').focus();};$('form').onsubmit=async e=>{e.preventDefault();if(busy)return;const text=$('input').value.trim();if(!text)return;busy=true;$('send').disabled=true;$('status').textContent='Thinking...';add('user',text);$('input').value='';try{const d=await api('/agent/query',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({message:text,workspace:$('workspace').value||null})});add('assistant',d.content||'');$('status').textContent='';load();}catch(e){$('status').textContent=e.message;}finally{busy=false;$('send').disabled=false;}};$('input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('form').requestSubmit();}};load();
</script></body></html>"""
CHAT_HTML = CHAT_HTML.replace("safe(x.session_id.slice(0,8))", "safe(x.title)")
CHAT_HTML = CHAT_HTML.replace("const d=await api('/sessions/'+encodeURIComponent(id));messages.innerHTML='';d.actions.filter(x=>x.tool==='').forEach(x=>add(x.status==='success'?'assistant':'assistant',x.result_preview||x.error||''));", "const events=await api('/sessions/'+encodeURIComponent(id)+'/events');messages.innerHTML='';events.filter(x=>['user_request','final_response'].includes(x.event_type)).forEach(x=>add(x.event_type==='user_request'?'user':'assistant',x.payload.content||''));")
CHAT_HTML = CHAT_HTML.replace(".side{padding:1rem", ".side{padding:1.25rem 1rem")
CHAT_HTML = CHAT_HTML.replace(".brand{font-size:1.25rem", ".brand{padding:.25rem 0;font-size:1.25rem")
CHAT_HTML = CHAT_HTML.replace(".session{padding:.55rem", ".session{padding:.7rem .6rem;line-height:1.3;border-bottom:1px solid #263441")
CHAT_HTML = CHAT_HTML.replace("const $=id=>document.getElementById(id),messages=$('messages');let busy=false;", "const $=id=>document.getElementById(id),messages=$('messages');let busy=false,currentSessionId=null;")
CHAT_HTML = CHAT_HTML.replace("messages.innerHTML='';events.filter", "currentSessionId=id;messages.innerHTML='';events.filter")
CHAT_HTML = CHAT_HTML.replace("$('new').onclick=()=>{messages.innerHTML=", "$('new').onclick=()=>{currentSessionId=null;messages.innerHTML=")
CHAT_HTML = CHAT_HTML.replace("$('workspace').innerHTML='<option value=\"\">None</option>'+ws.filter", "const activeWorkspace=$('workspace').value;$('workspace').innerHTML='<option value=\"\">None</option>'+ws.filter")
CHAT_HTML = CHAT_HTML.replace(".join('');document.querySelectorAll('[data-session]')", ".join('');if(activeWorkspace)$('workspace').value=activeWorkspace;$('workspace').disabled=!!currentSessionId;document.querySelectorAll('[data-session]')")
CHAT_HTML = CHAT_HTML.replace(".join('');$('sessions').innerHTML=", ".join('');if(activeWorkspace)$('workspace').value=activeWorkspace;$('workspace').disabled=!!currentSessionId;$('sessions').innerHTML=")
CHAT_HTML = CHAT_HTML.replace("currentSessionId=id;messages.innerHTML='';events.filter", "currentSessionId=id;messages.innerHTML='';const bound=events.find(x=>x.event_type==='user_request');if(bound&&bound.payload.workspace){$('workspace').value=bound.payload.workspace;}$('workspace').disabled=true;events.filter")
CHAT_HTML = CHAT_HTML.replace("$('new').onclick=()=>{currentSessionId=null;messages.innerHTML=", "$('new').onclick=()=>{currentSessionId=null;$('workspace').disabled=false;messages.innerHTML=")
CHAT_HTML = CHAT_HTML.replace("body:JSON.stringify({message:text,workspace:$('workspace').value||null})", "body:JSON.stringify({message:text,session_id:currentSessionId,workspace:$('workspace').value||null})")
CHAT_HTML = CHAT_HTML.replace("add('assistant',d.content||'');$('status')", "currentSessionId=d.session_id;add('assistant',d.content||'');$('status')")
CHAT_HTML = CHAT_HTML.replace("</script></body></html>", """const oldSubmit=$('form').onsubmit;$('form').onsubmit=async e=>{e.preventDefault();if(busy)return;const text=$('input').value.trim();if(!text)return;busy=true;$('send').disabled=true;add('user',text);$('input').value='';let answer='';try{const r=await fetch('/agent/query/stream',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({message:text,session_id:currentSessionId,workspace:$('workspace').value||null})});if(!r.ok)throw Error('Request failed');const reader=r.body.getReader(),decoder=new TextDecoder();let buffer='';while(true){const part=await reader.read();if(part.done)break;buffer+=decoder.decode(part.value,{stream:true});const chunks=buffer.split('\\n\\n');buffer=chunks.pop();for(const chunk of chunks){const line=chunk.split('\\n').find(x=>x.startsWith('data: '));if(!line)continue;const data=JSON.parse(line.slice(6)),type=chunk.split('\\n').find(x=>x.startsWith('event: '))?.slice(7);if(type==='session')currentSessionId=data.session_id;if(type==='status')$('status').textContent=data.message;if(type==='text_delta'){answer+=data.text;if(!messages.lastElementChild||!messages.lastElementChild.classList.contains('assistant'))add('assistant','');messages.lastElementChild.textContent=answer;}if(type==='completed'){answer=data.response||'';if(!messages.lastElementChild||!messages.lastElementChild.classList.contains('assistant'))add('assistant','');messages.lastElementChild.textContent=answer;}if(type==='error')throw Error(data.message);}}$('status').textContent='';load();}catch(err){$('status').textContent=err.message;}finally{busy=false;$('send').disabled=false;}};</script></body></html>""")

settings = Settings()

provider_registry = build_provider_registry(settings)

workspace_registry = build_workspace_registry(settings)
skill_service = SkillService()
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
navigator = WorkspaceNavigator(workspace_registry, KnowledgeStore(settings.knowledge_index_path))
code_search = WorkspaceCodeSearch(workspace_registry)
cross_workspace_service = CrossWorkspaceKnowledgeService(workspace_registry, knowledge_service)
action_journal = ActionJournal(
    settings.action_journal_path
)
memory_service = MemoryService(MemoryStore(settings.action_journal_path))
proposal_service = ProposalService(settings.action_journal_path, memory_service)
plan_service = PlanService(settings.action_journal_path)
tool_registry = build_tool_registry(
    filesystem, git, knowledge_service, memory_service, proposal_service, plan_service,
    settings.action_journal_path, cross_workspace=cross_workspace_service, navigator=navigator,
)
mcp_adapter = MCPAdapter(load_mcp_server_configs(Path(settings.action_journal_path).parent / "mcp_servers.json"))
execution_spec_service = ExecutionSpecService(settings.action_journal_path, plan_service, tool_registry)
execution_bridge = TrustedExecutionBridge(execution_spec_service, tool_registry)
plan_execution = PlanExecutionCoordinator(plan_service, tool_registry, action_journal, lambda workspace, scope, reason: project_history_service.capture_after_success(workspace, scope, reason))
autonomy_service = AutonomousContinuationService(plan_service, plan_execution)
planned_execution_bridge = PlannedTaskExecutionBridge(plan_service, autonomy_service)
goal_decomposition_service = GoalDecompositionService(provider_registry, navigator, autonomy_service)
execution_trace_service = ExecutionTraceService(action_journal)
project_state_service = ProjectStateService(workspace_registry, git, memory_service, plan_service, execution_trace_service)
project_briefing_service = ProjectBriefingService(project_state_service, provider_registry, settings)
project_history_service = ProjectStateHistoryService(project_state_service, ProjectSnapshotStore(settings.action_journal_path))
sub_agent_service = SubAgentService(settings.action_journal_path, provider_registry, tool_registry, WorkerModelRouter(settings, provider_registry))
coding_workflow_service = CodingWorkflowService(settings.action_journal_path, workspace_registry, git, sub_agent_service, plan_service, execution_spec_service, execution_bridge, plan_execution, tool_registry, traces=execution_trace_service)
coding_proposal_service = CodingProposalService(settings.action_journal_path, coding_workflow_service, workspace_registry, git)
coding_job_service = CodingJobService(coding_workflow_service, coding_proposal_service, execution_spec_service, execution_trace_service, tool_registry)
coding_action_service = SupervisorCodingActionService(coding_job_service, coding_workflow_service, coding_proposal_service, tool_registry)
coding_guidance_service = CodingGuidanceService(coding_job_service)
supervisor_workflow_service = SupervisorResearchVerifyWorkflow(settings.action_journal_path, sub_agent_service)
research_action_service = ResearchWorkflowActionService(supervisor_workflow_service)
team_coordinator = AutonomousTeamCoordinator(settings.action_journal_path, plan_service, autonomy_service, TeamReconciler(settings, provider_registry, sub_agent_service.source_evidence), result_loader=lambda plan_id, scope: planned_analysis.saved_results(plan_id, scope))
planned_analysis = PlannedAnalysis(plan_service, sub_agent_service)
tool_registry.register(ToolDefinition(name="autonomy.analyze", description="Internal scoped planned analysis", permission="read", handler=planned_analysis.run, internal=True))
planned_worker_dispatcher = PlannedWorkerDispatcher(coding=coding_workflow_service, research=supervisor_workflow_service, teams=team_coordinator)
planned_execution_bridge = PlannedTaskExecutionBridge(plan_service, autonomy_service, planned_worker_dispatcher, analysis_tool="autonomy.analyze")
supervisor_policy = SupervisorPolicy()
supervisor_dashboard_service = SupervisorDashboardService(workspace_registry, project_state_service, coding_workflow_service, coding_job_service, coding_guidance_service, supervisor_workflow_service, plan_service, execution_trace_service, tool_registry)
async def _create_chat_plan(goal: str, scope: str, workspace: str, read_only: bool) -> ProjectPlan:
    _output, plan = await goal_decomposition_service.decompose(
        GoalRequest(goal=goal, scope=scope, workspace=workspace, read_only=read_only),
        provider_name=settings.default_provider, model=get_default_model(settings, settings.default_provider),
    )
    return plan


def _chat_workflow_records(plan: ProjectPlan) -> list[dict]:
    records = []
    for workflow in coding_workflow_service.list(plan.scope, plan.workspace, 100):
        if workflow.parent_plan_id == plan.id:
            job = coding_job_service.get(workflow.workflow_id, plan.scope)
            records.append({"workflow_id": workflow.workflow_id, "proposal_id": job.proposal_id if job else None,
                            "status": job.status if job else workflow.status})
    return records


def _cancel_chat_plan(plan: ProjectPlan) -> None:
    # Cancel only children owned by this exact scoped parent. No file operations.
    autonomy_service.cancel(plan.id, plan.scope)
    for workflow in coding_workflow_service.list(plan.scope, plan.workspace, 100):
        if workflow.parent_plan_id == plan.id and workflow.status not in {"completed", "failed", "cancelled"}:
            coding_workflow_service.cancel(workflow.workflow_id, plan.scope, "Parent plan cancelled from chat")
    for workflow in supervisor_workflow_service.list(plan.scope, 100):
        if workflow.plan_id == plan.id and workflow.status not in {"completed", "failed", "cancelled"}:
            supervisor_workflow_service.cancel(workflow.workflow_id, plan.scope, "Parent plan cancelled from chat")


chat_planned_review = ChatPlannedReview(plan_service, autonomy_service, team_coordinator,
                                      lambda plan_id, scope: _continue_plan(plan_id, scope), workspace_registry.get_root,
                                      create_plan=_create_chat_plan, workflow_records=_chat_workflow_records,
                                      worker_records=lambda plan: sub_agent_service.for_plan(plan.id, plan.scope),
                                      cancel_plan=_cancel_chat_plan)

agent_service = AgentService(
    settings,
    provider_registry,
    tools=tool_registry,
    journal=action_journal,
    allow_tools_without_workspace=False,
    include_identity_context=True,
    skill_service=skill_service,
    skill_workspace_resolver=workspace_registry.get_root,
    planned_review_handler=chat_planned_review.handle,
)
diagnostics = build_local_diagnostics(settings)


@app.on_event("startup")
async def start_mcp_servers() -> None:
    """Connect configured MCP servers without making startup dependent on them."""
    for name in [status.name for status in mcp_adapter.statuses()]:
        try:
            mcp_adapter.connect_configured(name)
        except Exception:  # noqa: BLE001, S112 - optional integration boundary
            continue
    mcp_adapter.register_tools(tool_registry)


@app.on_event("shutdown")
async def stop_mcp_servers() -> None:
    for status in mcp_adapter.statuses():
        if status.connected:
            mcp_adapter.disconnect(status.name)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "rexroad-ai",
        "version": "0.1.0",
    }


@app.get("/skills")
async def list_skills(workspace: str | None = None) -> list[dict[str, object]]:
    root = workspace_registry.get_root(workspace) if workspace else None
    skills, _errors = skill_service.discover(root)
    return [
        {"name": skill.name, "description": skill.description, "version": skill.version,
         "origin": skill.origin, "user_invocable": skill.user_invocable,
         "model_invocable": skill.model_invocable}
        for skill in skills[:100]
    ]


@app.get("/operator", response_class=HTMLResponse)
async def operator_page() -> HTMLResponse:
    shell = OPERATOR_HTML.split("</main>", 1)[0]
    shell = shell.replace("</style>", "input,select,pre{max-width:100%;box-sizing:border-box}pre{overflow:auto;white-space:pre-wrap}button:disabled{opacity:.5;cursor:default}</style>")
    return HTMLResponse(shell + '<script src="/operator-controls.js"></script><script src="/project-search.js"></script><script src="/project-file.js"></script></main></body></html>')


@app.get("/chat", response_class=HTMLResponse)
async def chat_page() -> HTMLResponse:
    return HTMLResponse(CHAT_HTML)


@app.get("/supervisor/dashboard", response_model=SupervisorProjectDashboard)
async def supervisor_dashboard(scope: str, workspace: str, activity_limit: int = 50) -> SupervisorProjectDashboard:
    try:
        return supervisor_dashboard_service.get(scope, workspace, activity_limit=activity_limit)
    except (KeyError, ValueError):
        raise HTTPException(status_code=404, detail="Dashboard unavailable") from None


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


@app.get("/mcp/status")
async def mcp_status() -> list[dict[str, object]]:
    return [status.model_dump() for status in mcp_adapter.statuses()]


@app.get("/v1/models")
async def openai_models() -> dict[str, object]:
    return {
        "object": "list",
        "data": [{"id": REXROAD_MODEL, "object": "model", "owned_by": "rexroad"}],
    }


@app.post("/v1/chat/completions")
async def openai_chat_completions(
    request: OpenAIChatRequest,
    x_rexroad_workspace: str | None = Header(default=None),
    x_rexroad_session_id: str | None = Header(default=None),
):
    validate_request(request)
    if x_rexroad_workspace is not None:
        try:
            workspace_registry.get_root(x_rexroad_workspace)
        except WorkspaceAccessError:
            raise HTTPException(status_code=404, detail={"error": {"message": "Workspace not found", "type": "invalid_request_error", "param": None, "code": "invalid_workspace"}}) from None
    agent_request = to_agent_request(request, session_id=x_rexroad_session_id, workspace=x_rexroad_workspace)
    if request.stream:
        completion_id = f"chatcmpl-{uuid4().hex}"

        async def events():
            try:
                async for item in stream_completion(agent_service, agent_request, completion_id):
                    yield f"data: {json.dumps(item, ensure_ascii=False, separators=(',', ':'))}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as exc:  # noqa: BLE001 - compatibility boundary returns safe failure
                error = safe_agent_error(exc).detail
                yield f"data: {json.dumps(error, ensure_ascii=False, separators=(',', ':'))}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")
    try:
        response = await agent_service.query(agent_request)
    except Exception as exc:  # noqa: BLE001 - compatibility boundary returns safe failure
        raise safe_agent_error(exc) from None
    result = completion_response(response)
    headers = {"X-Rexroad-Session-ID": response.session_id}
    return JSONResponse(result, headers=headers)


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


@app.get("/sessions/{session_id}/events")
async def session_events(session_id: str):
    if action_journal.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return action_journal.list_events_for_session(session_id)

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
    if request.workspace is not None:
        try:
            workspace_registry.get_root(request.workspace)
        except WorkspaceAccessError:
            raise HTTPException(status_code=404, detail="Workspace not found") from None
    try:
        return await agent_service.query(request)
    except AgentSessionError as exc:
        status = 404 if str(exc) == "Session not found" else 409
        raise HTTPException(status_code=status, detail=str(exc)) from None
    except AgentNoProgressError:
        raise HTTPException(status_code=422, detail="Repeated tool calls made no progress; the request was stopped") from None
    except AgentLoopLimitError:
        raise HTTPException(status_code=422, detail="The request exceeded the bounded tool-use limit") from None
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="The AI model service is unavailable or timed out. Start the local model with Start-RexroadAI.ps1, then retry in this chat. Saved work is preserved.") from None


@app.get("/operator-controls.js")
async def operator_controls() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "operator-controls.js", media_type="text/javascript")


@app.get("/project-search.js")
async def project_search_controls() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "project-search.js", media_type="text/javascript")


@app.get("/workspaces/{workspace}/search")
def search_workspace(workspace: str, query: str = Query(default="", max_length=200), pattern: str = Query(default="**/*", min_length=1, max_length=200), limit: int = Query(default=20, ge=1, le=50)) -> dict:
    try:
        if query:
            return code_search.grep(workspace, query, pattern=pattern, limit=limit)
        return code_search.glob(workspace, pattern=pattern, limit=limit)
    except PermissionError:
        raise HTTPException(status_code=404, detail="The selected workspace or search folder is unavailable") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/project-file.js")
async def project_file_controls() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "project-file.js", media_type="text/javascript")


@app.get("/workspaces/{workspace}/symbols")
def workspace_symbols(workspace: str, symbol: str = Query(min_length=1, max_length=100), kind: str = Query(default="definitions"), pattern: str = Query(default="*.py", min_length=1, max_length=200), limit: int = Query(default=20, ge=1, le=50)) -> dict:
    try:
        return PythonSymbols(code_search).find(workspace, symbol, kind, pattern, limit)
    except PermissionError:
        raise HTTPException(status_code=404, detail="The selected workspace is unavailable") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/workspaces/{workspace}/source")
def workspace_source(workspace: str, path: str = Query(min_length=1, max_length=1000), line: int = Query(default=1, ge=1, le=10_000_000)) -> dict:
    try:
        return code_search.source(workspace, path, line)
    except (PermissionError, OSError):
        raise HTTPException(status_code=404, detail="The selected source file is unavailable") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


_STREAM_TOOL_LABELS = {
    "filesystem.list": "Inspecting project files",
    "filesystem.read": "Reading project files",
    "filesystem.search": "Searching project files",
    "filesystem.glob": "Finding project files",
    "filesystem.grep": "Finding matching source lines",
    "git.status": "Checking Git status",
    "git.log": "Reading Git history",
    "knowledge.search": "Searching project knowledge",
    "workspace.run_check": "Running approved checks",
}


def _stream_event(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False, sort_keys=True)}\n\n"


@app.post("/agent/query/stream")
async def agent_query_stream(request: AgentQueryRequest) -> StreamingResponse:
    async def events():
        yield _stream_event("status", {"message": "Working on your request"})
        try:
            async for event in agent_service.query_stream(request):
                event_type = event.pop("type")
                if event_type == "tool_started":
                    event = {"tool": event["tool"], "label": _STREAM_TOOL_LABELS.get(event["tool"], "Using a project tool")}
                yield _stream_event(event_type, event)
        except AgentSessionError as exc:
            yield _stream_event("error", {"message": str(exc)[:200]})
        except AgentNoProgressError:
            yield _stream_event("error", {"message": "Repeated tool calls made no progress; the request was stopped"})
        except AgentLoopLimitError:
            yield _stream_event("error", {"message": "The request exceeded the bounded tool-use limit"})
        except httpx.RequestError:
            yield _stream_event("error", {"message": "The AI model service is unavailable or timed out. Start the local model with Start-RexroadAI.ps1, then retry in this chat. Saved work is preserved."})
        except Exception:  # noqa: BLE001 - streaming boundary returns safe failure
            yield _stream_event("error", {"message": "The request could not be completed safely"})

    return StreamingResponse(events(), media_type="text/event-stream")


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


@app.post("/supervisor/recommend-worker", response_model=SupervisorRecommendation)
async def recommend_worker(request: SupervisorRecommendationRequest) -> SupervisorRecommendation:
    return supervisor_policy.recommend(request)


@app.post("/supervisor/dispatch", response_model=SubAgentResult)
async def supervisor_dispatch(request: SupervisorDispatchRequest) -> SubAgentResult:
    try:
        authorization = sub_agent_service.authorize_dispatch(request)
        return await sub_agent_service.dispatch(request, authorization)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Dispatch authorization denied") from exc


@app.post("/supervisor-workflows/research-verify", response_model=ResearchVerifyWorkflow)
async def create_research_verify_workflow(request: ResearchVerifyWorkflowCreate) -> ResearchVerifyWorkflow:
    return supervisor_workflow_service.create(request)


@app.get("/supervisor-workflows", response_model=list[ResearchVerifyWorkflow])
async def list_research_verify_workflows(scope: str, limit: int = 20, status: str | None = None) -> list[ResearchVerifyWorkflow]:
    try:
        return supervisor_workflow_service.list(scope, limit, status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid workflow query") from exc


@app.get("/supervisor-workflows/{workflow_id}", response_model=ResearchVerifyWorkflowResult)
async def get_research_verify_workflow(workflow_id: str, scope: str) -> ResearchVerifyWorkflowResult:
    workflow = supervisor_workflow_service.get(workflow_id, scope)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return supervisor_workflow_service.result(workflow)


@app.post("/supervisor-workflows/{workflow_id}/start-research", response_model=ResearchVerifyWorkflowResult)
async def start_research_verify_workflow(workflow_id: str, scope: str) -> ResearchVerifyWorkflowResult:
    try:
        return await supervisor_workflow_service.start_research(workflow_id, scope)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Research workflow unavailable") from exc


@app.post("/supervisor-workflows/{workflow_id}/start-verification", response_model=ResearchVerifyWorkflowResult)
async def start_verification_workflow(workflow_id: str, scope: str) -> ResearchVerifyWorkflowResult:
    try:
        return await supervisor_workflow_service.start_verification(workflow_id, scope)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Verification workflow unavailable") from exc


def _continue_plan(plan_id: str, scope: str) -> dict:
    plan = plan_service.get(plan_id, scope)
    if plan is None:
        raise ValueError("plan not found")
    if plan.workspace:
        workspace_registry.get_root(plan.workspace)
    names = ("workspace.repo_map", "git.status", "filesystem.read", "autonomy.analyze")
    authorizations = {name: tool_registry.authorize(name, scope, "autonomy:" + plan_id) for name in names
                      if tool_registry.get(name).permission == "read" and not tool_registry.get(name).high_impact}
    return autonomy_service.continue_routed(plan_id=plan_id, scope=scope, bridge=planned_execution_bridge,
                                           teams=team_coordinator, authorizations=authorizations, max_steps=10)


async def _resume_research_plan(result: ResearchWorkflowActionResult) -> None:
    if result.plan_id:
        await asyncio.to_thread(_continue_plan, result.plan_id, result.scope)


@app.post("/supervisor-research-workflows/{workflow_id}/action", response_model=ResearchWorkflowActionResult)
async def dispatch_research_workflow_action(workflow_id: str, scope: str, request: ResearchWorkflowActionRequest) -> ResearchWorkflowActionResult:
    try:
        result = await research_action_service.dispatch(workflow_id, scope, request)
        await _resume_research_plan(result)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Research workflow action unavailable") from exc


@app.post("/supervisor-workflows/{workflow_id}/cancel", response_model=ResearchVerifyWorkflow)
async def cancel_research_verify_workflow(workflow_id: str, scope: str, reason: str | None = None) -> ResearchVerifyWorkflow:
    try:
        return supervisor_workflow_service.cancel(workflow_id, scope, reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Workflow cancellation unavailable") from exc


@app.get("/supervisor-workflows/{workflow_id}/result", response_model=ResearchVerifyWorkflowResult)
async def get_research_verify_result(workflow_id: str, scope: str) -> ResearchVerifyWorkflowResult:
    workflow = supervisor_workflow_service.get(workflow_id, scope)
    if workflow is None or workflow.final_outcome not in {"verified", "rejected"}:
        raise HTTPException(status_code=404, detail="Workflow result unavailable")
    return supervisor_workflow_service.result(workflow)


@app.post("/supervisor-workflows/{workflow_id}/accept-verifier", response_model=ResearchVerifyWorkflowResult)
async def accept_verifier_result(workflow_id: str, scope: str, reviewer_session_id: str | None = None, note: str | None = None) -> ResearchVerifyWorkflowResult:
    try:
        return supervisor_workflow_service.review_verifier(workflow_id, scope, "accepted", reviewer_session_id, note)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Verifier review unavailable") from exc


@app.post("/supervisor-workflows/{workflow_id}/reject-verifier", response_model=ResearchVerifyWorkflowResult)
async def reject_verifier_result(workflow_id: str, scope: str, reviewer_session_id: str | None = None, note: str | None = None) -> ResearchVerifyWorkflowResult:
    try:
        return supervisor_workflow_service.review_verifier(workflow_id, scope, "rejected", reviewer_session_id, note)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Verifier review unavailable") from exc


@app.get("/supervisor-dispatches", response_model=list[SupervisorDispatchAudit])
async def list_supervisor_dispatches(scope: str, limit: int = 20) -> list[SupervisorDispatchAudit]:
    return sub_agent_service.audits(scope, limit)


@app.get("/supervisor-dispatches/{dispatch_id}", response_model=SupervisorDispatchAudit)
async def get_supervisor_dispatch(dispatch_id: str, scope: str) -> SupervisorDispatchAudit:
    audit = sub_agent_service.audit(dispatch_id, scope)
    if audit is None:
        raise HTTPException(status_code=404, detail="Dispatch not found")
    return audit


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


@app.post("/sub-agent-contributions/{task_id}/incorporate", response_model=SubAgentIncorporation)
async def incorporate_sub_agent(task_id: str, scope: str, target_type: str, target_id: str, reviewer_session_id: str | None = None, note: str | None = None) -> SubAgentIncorporation:
    try:
        return sub_agent_service.incorporate(task_id, scope, target_type, target_id, note, reviewer_session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Contribution unavailable") from exc


@app.get("/sub-agent-incorporations/{incorporation_id}", response_model=SubAgentIncorporation)
async def get_sub_agent_incorporation(incorporation_id: str, scope: str) -> SubAgentIncorporation:
    item = sub_agent_service.get_incorporation(incorporation_id, scope)
    if item is None:
        raise HTTPException(status_code=404, detail="Incorporation not found")
    return item


@app.post("/sub-agent-incorporations/{incorporation_id}/revoke", response_model=SubAgentIncorporation)
async def revoke_sub_agent_incorporation(incorporation_id: str, scope: str) -> SubAgentIncorporation:
    try:
        return sub_agent_service.revoke_incorporation(incorporation_id, scope)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Incorporation not found") from exc


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


@app.get("/autonomy/plans/{plan_id}")
async def autonomy_status(plan_id: str, scope: str) -> dict[str, object]:
    try:
        status = autonomy_service.inspect(plan_id, scope)
        status["team"] = team_coordinator.get_for_plan(plan_id, scope)
        return status
    except ValueError:
        raise HTTPException(status_code=404, detail="Autonomous plan not found") from None


@app.post("/autonomy/decompose")
async def decompose_autonomous_goal(request: GoalRequest) -> dict[str, object]:
    try:
        output, plan = await goal_decomposition_service.decompose(request, provider_name=settings.default_provider, model=get_default_model(settings, settings.default_provider))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:300]) from None
    return {"plan": plan, "decomposition": output}


@app.post("/autonomy/plans/{plan_id}/cancel", response_model=ProjectPlan)
async def cancel_autonomy(plan_id: str, scope: str) -> ProjectPlan:
    try:
        plan = autonomy_service.cancel(plan_id, scope)
    except ValueError:
        raise HTTPException(status_code=409, detail="Autonomous plan cannot be cancelled") from None
    if plan is None:
        raise HTTPException(status_code=404, detail="Autonomous plan not found")
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


@app.post("/supervisor-workflows/coding", response_model=CodingWorkflow)
async def create_coding_workflow(request: CodingWorkflowCreate) -> CodingWorkflow:
    try:
        return coding_workflow_service.create(request)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail="Invalid coding workflow request") from exc


@app.get("/supervisor-coding-workflows/{workflow_id}", response_model=CodingWorkflow)
async def get_coding_workflow(workflow_id: str, scope: str) -> CodingWorkflow:
    item = coding_workflow_service.get(workflow_id, scope)
    if item is None:
        raise HTTPException(status_code=404, detail="Coding workflow not found")
    return item


@app.post("/supervisor-coding-workflows/{workflow_id}/start-analysis", response_model=CodingWorkflow)
async def start_coding_analysis(workflow_id: str, scope: str) -> CodingWorkflow:
    try:
        return await coding_workflow_service.start_analysis(workflow_id, scope)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Coding analysis unavailable") from exc


@app.post("/supervisor-coding-workflows/{workflow_id}/prepare", response_model=CodingWorkflow)
async def prepare_coding_implementation(workflow_id: str, scope: str, patches: list[PatchAction]) -> CodingWorkflow:
    try:
        return coding_workflow_service.prepare(workflow_id, scope, patches)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=409, detail="Implementation preparation unavailable") from exc


@app.post("/supervisor-coding-workflows/{workflow_id}/prepare-checks", response_model=CodingWorkflow)
async def prepare_coding_checks(workflow_id: str, scope: str, checks: list[CheckAction]) -> CodingWorkflow:
    try:
        return coding_workflow_service.prepare_checks(workflow_id, scope, checks)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Check preparation unavailable") from exc


@app.post("/supervisor-coding-workflows/{workflow_id}/execute-checks", response_model=CodingWorkflow)
async def execute_coding_checks(workflow_id: str, scope: str) -> CodingWorkflow:
    try:
        return coding_workflow_service.execute_checks(workflow_id, scope)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Check execution unavailable") from exc


@app.post("/supervisor-coding-workflows/{workflow_id}/start-verification", response_model=CodingWorkflow)
async def start_coding_verification(workflow_id: str, scope: str) -> CodingWorkflow:
    try:
        return await coding_workflow_service.start_verification(workflow_id, scope)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Verification unavailable") from exc


@app.post("/supervisor-coding-workflows/{workflow_id}/accept-verifier", response_model=CodingWorkflow)
async def accept_coding_verifier(workflow_id: str, scope: str, reviewer_session_id: str | None = None, note: str | None = None) -> CodingWorkflow:
    try:
        return coding_workflow_service.review_verifier(workflow_id, scope, "accepted", reviewer_session_id, note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Verifier review unavailable") from exc


@app.post("/supervisor-coding-workflows/{workflow_id}/reject-verifier", response_model=CodingWorkflow)
async def reject_coding_verifier(workflow_id: str, scope: str, reviewer_session_id: str | None = None, note: str | None = None) -> CodingWorkflow:
    try:
        return coding_workflow_service.review_verifier(workflow_id, scope, "rejected", reviewer_session_id, note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Verifier review unavailable") from exc


@app.post("/supervisor-coding-workflows/{workflow_id}/cancel", response_model=CodingWorkflow)
async def cancel_coding_workflow(workflow_id: str, scope: str, reason: str | None = None) -> CodingWorkflow:
    try:
        return coding_workflow_service.cancel(workflow_id, scope, reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Coding workflow not found") from exc


def _coding_proposal_for_workflow(workflow_id: str, scope: str) -> CodingProposal | None:
    job = coding_job_service.get(workflow_id, scope)
    return coding_proposal_service.get(job.proposal_id, scope) if job and job.proposal_id else None


@app.post("/supervisor-coding-workflows/{workflow_id}/proposal", response_model=CodingProposal)
async def create_coding_proposal(workflow_id: str, scope: str, request: ProposalCreate) -> CodingProposal:
    try:
        return coding_proposal_service.create(workflow_id, scope, request)
    except ValueError as exc:
        detail = str(exc) if str(exc).startswith("Requested test") else "Proposal unavailable"
        raise HTTPException(status_code=409, detail=detail) from None


_coding_generation_locks: dict[str, asyncio.Lock] = {}


class CodingProposalGenerationRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


@app.post("/supervisor-coding-workflows/{workflow_id}/proposal/generate", response_model=CodingProposal)
async def generate_coding_proposal(workflow_id: str, scope: str, request: CodingProposalGenerationRequest | None = None) -> CodingProposal:
    async with _coding_generation_locks.setdefault(workflow_id, asyncio.Lock()):
        try:
            return await coding_proposal_service.generate(workflow_id, scope, provider_registry.get(settings.default_provider), get_default_model(settings, settings.default_provider), request.note if request else None)
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="The AI model service is unavailable or timed out. Saved analysis is preserved; restart the local model and retry.") from None
        except ValueError as exc:
            reason = str(exc)
            if reason.startswith("structured output validation failed") or reason == "structured response exceeded the size limit":
                detail = "The model did not return a valid bounded proposal. Saved analysis is preserved; retry proposal generation."
            elif reason == "proposal must quote unique text from reviewed source":
                detail = "The proposal did not match the saved source exactly. No files changed; retry proposal generation."
            elif reason.startswith("Requested test"):
                detail = reason + ". No proposal was saved and no files changed. Retry with the requested tests included."
            else:
                detail = "A proposal requires accepted analysis, complete source evidence, and unchanged target files. Review the saved worker report before retrying."
            raise HTTPException(status_code=409, detail=detail) from None


@app.get("/supervisor-coding-workflows/{workflow_id}/proposal", response_model=CodingProposal)
async def get_coding_proposal(workflow_id: str, scope: str) -> CodingProposal:
    item = _coding_proposal_for_workflow(workflow_id, scope)
    if item is None: raise HTTPException(status_code=404, detail="Proposal not found")
    return item

@app.get("/supervisor-coding-workflows/{workflow_id}/job", response_model=CodingJob)
async def get_coding_job(workflow_id: str, scope: str) -> CodingJob:
    job = coding_job_service.get(workflow_id, scope)
    if job is None:
        raise HTTPException(status_code=404, detail="Coding job not found")
    return job

@app.get("/supervisor-coding-workflows/{workflow_id}/guidance", response_model=CodingJobGuidance)
async def get_coding_guidance(workflow_id: str, scope: str) -> CodingJobGuidance:
    guidance = coding_guidance_service.get(workflow_id, scope)
    if guidance is None:
        raise HTTPException(status_code=404, detail="Coding guidance not found")
    return guidance

@app.post("/supervisor-coding-workflows/{workflow_id}/action", response_model=CodingJobActionResult)
async def dispatch_coding_job_action(workflow_id: str, request: CodingJobActionRequest) -> CodingJobActionResult:
    try:
        result = await coding_action_service.dispatch(workflow_id, request)
        await _resume_coding_workflow(workflow_id, request.scope)
        return result
    except ValueError as exc:
        detail = str(exc) if str(exc).startswith("Verification conflicts:") else "Coding job action unavailable"
        raise HTTPException(status_code=409, detail=detail) from None


async def _resume_coding_workflow(workflow_id: str, scope: str) -> None:
    for _ in range(3):
        workflow = coding_workflow_service.get(workflow_id, scope)
        if workflow is None:
            return
        if not workflow.parent_plan_id:
            return
        await asyncio.to_thread(_continue_plan, workflow.parent_plan_id, scope)
        job = coding_job_service.get(workflow_id, scope)
        if job is None or job.next_action.action not in {"execute_patches", "execute_checks", "start_verifier"} or not job.next_action.allowed:
            return
        await coding_action_service.dispatch(workflow_id, CodingJobActionRequest(action=job.next_action.action, scope=scope))

@app.get("/supervisor-coding-workflows/{workflow_id}/proposal/preview")
async def preview_coding_proposal(workflow_id: str, scope: str) -> dict:
    item = _coding_proposal_for_workflow(workflow_id, scope)
    if item is None:
        raise HTTPException(status_code=404, detail="Coding proposal not found")
    try: return coding_proposal_service.preview(item.proposal_id, scope)
    except ValueError as exc: raise HTTPException(status_code=404, detail="Coding proposal not found") from exc

@app.get("/supervisor-coding-workflows/{workflow_id}/proposals")
async def list_coding_proposals(workflow_id: str, scope: str, limit: int = 5) -> list[dict]:
    return coding_proposal_service.history(workflow_id, scope, limit)

@app.post("/supervisor-coding-workflows/{workflow_id}/proposal/revise")
async def revise_coding_proposal(workflow_id: str, scope: str, note: str, proposal_id: str | None = None) -> dict:
    history = coding_proposal_service.history(workflow_id, scope, 5)
    selected_id = proposal_id or (history[-1]["proposal_id"] if history else None)
    item = coding_proposal_service.get(selected_id, scope) if selected_id else None
    if item is None or item.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="Coding proposal not found")
    try: return await coding_proposal_service.request_revision(item.proposal_id, scope, note)
    except ValueError as exc: raise HTTPException(status_code=409, detail="Proposal cannot be revised") from exc

@app.get("/supervisor-coding-workflows/{workflow_id}/proposal/revision-candidate")
async def get_revision_candidate(workflow_id: str, scope: str) -> dict:
    try: return coding_proposal_service.revision_candidate(workflow_id, scope)
    except ValueError as exc: raise HTTPException(status_code=409, detail="Revision candidate unavailable") from exc

@app.post("/supervisor-coding-workflows/{workflow_id}/proposal/revision-candidate/materialize", response_model=CodingProposal)
async def materialize_revision_candidate(workflow_id: str, scope: str) -> CodingProposal:
    try: return coding_proposal_service.materialize_revision_candidate(workflow_id, scope)
    except ValueError as exc: raise HTTPException(status_code=409, detail="Revision candidate cannot be materialized") from exc


@app.post("/supervisor-coding-workflows/{workflow_id}/proposal/accept", response_model=CodingProposal)
async def accept_coding_proposal(workflow_id: str, scope: str, reviewer_session_id: str | None = None) -> CodingProposal:
    item = _coding_proposal_for_workflow(workflow_id, scope)
    if item is None: raise HTTPException(status_code=404, detail="Proposal not found")
    try: return coding_proposal_service.review(item.proposal_id, scope, "accepted", reviewer_session_id)
    except ValueError as exc: raise HTTPException(status_code=409, detail="Proposal review unavailable") from exc


@app.post("/supervisor-coding-workflows/{workflow_id}/proposal/reject", response_model=CodingProposal)
async def reject_coding_proposal(workflow_id: str, scope: str, reviewer_session_id: str | None = None, note: str | None = None) -> CodingProposal:
    item = _coding_proposal_for_workflow(workflow_id, scope)
    if item is None: raise HTTPException(status_code=404, detail="Proposal not found")
    try: return coding_proposal_service.review(item.proposal_id, scope, "rejected", reviewer_session_id, note)
    except ValueError as exc: raise HTTPException(status_code=409, detail="Proposal review unavailable") from exc


@app.post("/supervisor-coding-workflows/{workflow_id}/proposal/convert", response_model=CodingProposal)
async def convert_coding_proposal(workflow_id: str, scope: str, workspace: str) -> CodingProposal:
    item = _coding_proposal_for_workflow(workflow_id, scope)
    if item is None: raise HTTPException(status_code=404, detail="Proposal not found")
    try: return coding_proposal_service.convert(item.proposal_id, scope, workspace)
    except ValueError as exc: raise HTTPException(status_code=409, detail="Proposal conversion unavailable") from exc


@app.get("/supervisor-coding-workflows/{workflow_id}/proposal/spec-review")
async def get_coding_spec_review(workflow_id: str, scope: str) -> dict:
    item = _coding_proposal_for_workflow(workflow_id, scope)
    if item is None: raise HTTPException(status_code=404, detail="Proposal not found")
    try: return coding_proposal_service.spec_review(item.proposal_id, scope)
    except ValueError as exc: raise HTTPException(status_code=409, detail="Spec review unavailable") from exc


@app.post("/supervisor-coding-workflows/{workflow_id}/proposal/spec-review/accept", response_model=CodingProposal)
async def accept_coding_spec_review(workflow_id: str, scope: str, workspace: str, reviewer_session_id: str | None = None) -> CodingProposal:
    item = _coding_proposal_for_workflow(workflow_id, scope)
    if item is None: raise HTTPException(status_code=404, detail="Proposal not found")
    try: return coding_proposal_service.accept_specs(item.proposal_id, scope, workspace, reviewer_session_id)
    except ValueError as exc: raise HTTPException(status_code=409, detail="Spec review unavailable") from exc


@app.post("/supervisor-coding-workflows/{workflow_id}/proposal/spec-review/reject", response_model=CodingProposal)
async def reject_coding_spec_review(workflow_id: str, scope: str, reviewer_session_id: str | None = None) -> CodingProposal:
    item = _coding_proposal_for_workflow(workflow_id, scope)
    if item is None: raise HTTPException(status_code=404, detail="Proposal not found")
    try: return coding_proposal_service.reject_specs(item.proposal_id, scope, reviewer_session_id)
    except ValueError as exc: raise HTTPException(status_code=409, detail="Spec review unavailable") from exc


