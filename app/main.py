from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.agents.models import AgentQueryRequest, AgentQueryResponse
from app.agents.service import AgentService, AgentSessionError
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
from app.tools.factory import build_tool_registry
from app.tools.filesystem import ReadOnlyFilesystem
from app.tools.git import ReadOnlyGit
from app.tools.output_policy import sanitize_output
from app.tools.registry import ToolApprovalRequest
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
<section class="card"><h2>New supervised coding task</h2><input id="objective" maxlength="4000" size="60" placeholder="Describe the coding objective"><button id="create">Create coding workflow</button></section>
<section class="card"><h2>Coding Jobs</h2><div id="jobs" class="muted">None.</div></section>
<section class="card"><h2>Recent Activity</h2><div id="activity" class="muted">None.</div></section>
<script>
const $=id=>document.getElementById(id); let selected=null;
function safe(v){return String(v??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));}
async function api(url,options){const r=await fetch(url,options);let d={};try{d=await r.json()}catch{}if(!r.ok)throw new Error(d.detail||'Request failed');return d;}
async function loadWorkspaces(){const ws=await api('/workspaces');$('workspace').innerHTML=ws.map(x=>`<option value="${safe(x.name)}">${safe(x.name)}</option>`).join('');if(ws.length)load();}
function params(){return new URLSearchParams({scope:$('scope').value.trim(),workspace:$('workspace').value});}
async function load(){if(!$('workspace').value)return;try{const p=params(),d=await api('/supervisor/dashboard?'+p);$('project').innerHTML=`<b>${safe(d.project.workspace)}</b> · ${safe(d.project.branch||'no Git branch')} · ${d.project.clean===true?'clean':'dirty/unavailable'}<br>HEAD ${safe(d.project.head||'unavailable')}`;$('attention').innerHTML=d.attention.length?d.attention.map(x=>`<div class="attention ${safe(x.category)}"><b>${safe(x.category)}</b> · ${safe(x.headline)}<br>${safe(x.reason)}<br><span class="muted">${safe(x.next_action||'No action')}</span></div>`).join(''):'<span class="muted">Nothing requires attention.</span>';$('jobs').innerHTML=d.coding.length?d.coding.map(x=>`<div class="attention"><b>${safe(x.workflow_id)}</b> · ${safe(x.status)}<br>${safe(x.guidance_headline)}<br>${safe(x.blocking_reason||'')}<br><span class="files">${x.affected_resources.map(safe).join('\n')}</span><br><button data-job="${safe(x.workflow_id)}">Open guidance</button></div>`).join(''):'<span class="muted">No coding jobs.</span>';$('activity').innerHTML=d.recent_activity.length?d.recent_activity.map(x=>`<div>${safe(x.timestamp)} · ${safe(x.summary)} · ${safe(x.trace_id||'')}</div>`).join(''):'<span class="muted">No recent activity.</span>';document.querySelectorAll('[data-job]').forEach(b=>b.onclick=()=>selectJob(b.dataset.job));$('error').textContent='';}catch(e){$('error').textContent=e.message;}}
async function selectJob(id){selected=id;try{const p=params(),j=await api(`/supervisor-coding-workflows/${encodeURIComponent(id)}/job?${p}`),g=await api(`/supervisor-coding-workflows/${encodeURIComponent(id)}/guidance?${p}`);$('jobs').innerHTML=`<div class="attention"><h3>${safe(j.workflow_id)} · ${safe(j.status)}</h3><p>${safe(g.headline)}</p><p>${safe(g.explanation)}</p>${g.high_impact?`<div class="warning">This action modifies ${g.affected_resources.length} workspace file(s). Checks will not run automatically; Git will not be committed or pushed; retries will not occur automatically.</div>`:''}<div class="files">${g.affected_resources.map(safe).join('\n')}</div><p class="muted">Will do: ${g.will_do.map(safe).join('; ')}</p><p class="muted">Will not do: ${g.will_not_do.map(safe).join('; ')}</p><button id="action" data-action="${safe(g.next_action)}">${safe(g.next_action)}</button></div>`;$('action').onclick=()=>runAction(g.next_action);}catch(e){$('error').textContent=e.message;}}
async function runAction(action){if(!selected)return;const body={action,scope:$('scope').value.trim()};if(action.startsWith('review_'))body.decision=confirm('Accept this explicit review?')?'accept':'reject';if(action==='request_revision')body.note=prompt('Bounded revision note:')||'';try{await api(`/supervisor-coding-workflows/${encodeURIComponent(selected)}/action`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});await load();await selectJob(selected);}catch(e){$('error').textContent=e.message;await load();}}
$('refresh').onclick=load;$('workspace').onchange=load;$('create').onclick=async()=>{try{const objective=$('objective').value.trim();if(!objective)throw new Error('Objective required');const d=await api('/supervisor-workflows/coding',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({scope:$('scope').value.trim(),workspace:$('workspace').value,instruction:objective})});selected=d.workflow_id;$('objective').value='';await load();await selectJob(selected);}catch(e){$('error').textContent=e.message;}};loadWorkspaces();
</script></main></body></html>"""

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
CHAT_HTML = CHAT_HTML.replace("body:JSON.stringify({message:text,workspace:$('workspace').value||null})", "body:JSON.stringify({message:text,session_id:currentSessionId,workspace:$('workspace').value||null})")
CHAT_HTML = CHAT_HTML.replace("add('assistant',d.content||'');$('status')", "currentSessionId=d.session_id;add('assistant',d.content||'');$('status')")

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
sub_agent_service = SubAgentService(settings.action_journal_path, provider_registry, tool_registry, WorkerModelRouter(settings, provider_registry))
coding_workflow_service = CodingWorkflowService(settings.action_journal_path, workspace_registry, git, sub_agent_service, plan_service, execution_spec_service, execution_bridge, plan_execution, tool_registry)
coding_proposal_service = CodingProposalService(settings.action_journal_path, coding_workflow_service, workspace_registry, git)
coding_job_service = CodingJobService(coding_workflow_service, coding_proposal_service, execution_spec_service, execution_trace_service, tool_registry)
coding_action_service = SupervisorCodingActionService(coding_job_service, coding_workflow_service, coding_proposal_service, tool_registry)
coding_guidance_service = CodingGuidanceService(coding_job_service)
supervisor_workflow_service = SupervisorResearchVerifyWorkflow(settings.action_journal_path, sub_agent_service)
supervisor_policy = SupervisorPolicy()
supervisor_dashboard_service = SupervisorDashboardService(workspace_registry, project_state_service, coding_workflow_service, coding_job_service, coding_guidance_service, supervisor_workflow_service, plan_service, execution_trace_service, tool_registry)

agent_service = AgentService(
    settings,
    provider_registry,
    tools=tool_registry,
    journal=action_journal,
    allow_tools_without_workspace=False,
)
diagnostics = build_local_diagnostics(settings)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "rexroad-ai",
        "version": "0.1.0",
    }


@app.get("/operator", response_class=HTMLResponse)
async def operator_page() -> HTMLResponse:
    return HTMLResponse(OPERATOR_HTML)


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


@app.post("/supervisor-coding-workflows/{workflow_id}/proposal", response_model=CodingProposal)
async def create_coding_proposal(workflow_id: str, scope: str, request: ProposalCreate) -> CodingProposal:
    try:
        return coding_proposal_service.create(workflow_id, scope, request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Proposal unavailable") from exc


@app.get("/supervisor-coding-workflows/{workflow_id}/proposal", response_model=CodingProposal)
async def get_coding_proposal(workflow_id: str, scope: str) -> CodingProposal:
    item = coding_proposal_service.get(workflow_id, scope)
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
        return await coding_action_service.dispatch(workflow_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Coding job action unavailable") from exc

@app.get("/supervisor-coding-workflows/{workflow_id}/proposal/preview")
async def preview_coding_proposal(workflow_id: str, scope: str) -> dict:
    item = coding_proposal_service.get(workflow_id, scope)
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
    item = coding_proposal_service.get(workflow_id, scope)
    if item is None: raise HTTPException(status_code=404, detail="Proposal not found")
    try: return coding_proposal_service.review(item.proposal_id, scope, "accepted", reviewer_session_id)
    except ValueError as exc: raise HTTPException(status_code=409, detail="Proposal review unavailable") from exc


@app.post("/supervisor-coding-workflows/{workflow_id}/proposal/reject", response_model=CodingProposal)
async def reject_coding_proposal(workflow_id: str, scope: str, reviewer_session_id: str | None = None, note: str | None = None) -> CodingProposal:
    item = coding_proposal_service.get(workflow_id, scope)
    if item is None: raise HTTPException(status_code=404, detail="Proposal not found")
    try: return coding_proposal_service.review(item.proposal_id, scope, "rejected", reviewer_session_id, note)
    except ValueError as exc: raise HTTPException(status_code=409, detail="Proposal review unavailable") from exc


@app.post("/supervisor-coding-workflows/{workflow_id}/proposal/convert", response_model=CodingProposal)
async def convert_coding_proposal(workflow_id: str, scope: str, workspace: str) -> CodingProposal:
    item = coding_proposal_service.get(workflow_id, scope)
    if item is None: raise HTTPException(status_code=404, detail="Proposal not found")
    try: return coding_proposal_service.convert(item.proposal_id, scope, workspace)
    except ValueError as exc: raise HTTPException(status_code=409, detail="Proposal conversion unavailable") from exc


@app.get("/supervisor-coding-workflows/{workflow_id}/proposal/spec-review")
async def get_coding_spec_review(workflow_id: str, scope: str) -> dict:
    item = coding_proposal_service.get(workflow_id, scope)
    if item is None: raise HTTPException(status_code=404, detail="Proposal not found")
    try: return coding_proposal_service.spec_review(item.proposal_id, scope)
    except ValueError as exc: raise HTTPException(status_code=409, detail="Spec review unavailable") from exc


@app.post("/supervisor-coding-workflows/{workflow_id}/proposal/spec-review/accept", response_model=CodingProposal)
async def accept_coding_spec_review(workflow_id: str, scope: str, workspace: str, reviewer_session_id: str | None = None) -> CodingProposal:
    item = coding_proposal_service.get(workflow_id, scope)
    if item is None: raise HTTPException(status_code=404, detail="Proposal not found")
    try: return coding_proposal_service.accept_specs(item.proposal_id, scope, workspace, reviewer_session_id)
    except ValueError as exc: raise HTTPException(status_code=409, detail="Spec review unavailable") from exc


@app.post("/supervisor-coding-workflows/{workflow_id}/proposal/spec-review/reject", response_model=CodingProposal)
async def reject_coding_spec_review(workflow_id: str, scope: str, reviewer_session_id: str | None = None) -> CodingProposal:
    item = coding_proposal_service.get(workflow_id, scope)
    if item is None: raise HTTPException(status_code=404, detail="Proposal not found")
    try: return coding_proposal_service.reject_specs(item.proposal_id, scope, reviewer_session_id)
    except ValueError as exc: raise HTTPException(status_code=409, detail="Spec review unavailable") from exc


