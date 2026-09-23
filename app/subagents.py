from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from app.providers.models import ModelMessage, ModelRequest
from app.providers.registry import ProviderRegistry
from app.supervisor_policy import SupervisorPolicy, SupervisorRecommendationRequest
from app.tools.output_policy import sanitize_output
from app.tools.registry import ToolRegistry

PROFILES: dict[str, frozenset[str]] = {
    "researcher": frozenset({"knowledge.search", "knowledge.search_across_workspaces"}),
    "code_analyst": frozenset({"filesystem.read", "git.status", "git.log", "knowledge.search"}),
    "verifier": frozenset({"git.status", "git.diff", "knowledge.search"}),
}


class SubAgentTaskCreate(BaseModel):
    worker_profile: str
    scope: str
    instruction: str = Field(min_length=1, max_length=4000)
    allowed_tools: list[str] = Field(default_factory=list, max_length=10)
    workspace: str | None = None
    parent_session_id: str | None = None
    plan_id: str | None = None
    step_id: str | None = None


class SubAgentTask(BaseModel):
    task_id: str
    worker_profile: str
    scope: str
    workspace: str | None
    instruction: str
    allowed_tools: list[str]
    status: str
    parent_session_id: str | None
    plan_id: str | None
    step_id: str | None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class SubAgentResult(BaseModel):
    task_id: str
    worker_profile: str
    status: str
    summary: str = ""
    references: list[str] = Field(default_factory=list)
    tool_usage: list[str] = Field(default_factory=list)
    safe_reason: str | None = None
    started_at: datetime
    completed_at: datetime


class SubAgentReview(BaseModel):
    task_id: str
    scope: str
    status: str
    reviewer_session_id: str | None = None
    note: str | None = None
    reviewed_at: datetime


class SubAgentContribution(BaseModel):
    task_id: str
    worker_profile: str
    scope: str
    workspace: str | None
    summary: str
    references: list[str]
    tool_usage: list[str]
    parent_session_id: str | None
    plan_id: str | None
    step_id: str | None
    accepted_at: datetime


class SubAgentIncorporation(BaseModel):
    incorporation_id: str
    task_id: str
    scope: str
    workspace: str | None
    target_type: str
    target_id: str
    parent_session_id: str | None
    plan_id: str | None
    step_id: str | None
    status: str
    note: str | None
    created_at: datetime


class DispatchAuthorization:
    def __init__(self, profile: str, scope: str, workspace: str | None, session: str | None, fingerprint: str) -> None:
        self.token = str(uuid4())
        self.profile, self.scope, self.workspace, self.session, self.fingerprint = profile, scope, workspace, session, fingerprint


class SupervisorDispatchRequest(BaseModel):
    worker_profile: str
    scope: str
    instruction: str = Field(min_length=1, max_length=4000)
    workspace: str | None = None
    allowed_tools: list[str] = Field(default_factory=list, max_length=10)
    parent_session_id: str | None = None
    plan_id: str | None = None
    step_id: str | None = None


class SupervisorDispatchAudit(BaseModel):
    dispatch_id: str
    task_id: str | None
    scope: str
    workspace: str | None
    recommendation_category: str
    recommended_profile: str | None
    authorized_profile: str | None
    parent_session_id: str | None
    plan_id: str | None
    step_id: str | None
    instruction_fingerprint: str
    status: str
    safe_reason: str | None
    created_at: datetime
    completed_at: datetime | None = None
    tool_usage: list[dict] = Field(default_factory=list)


class SubAgentService:
    MAX_TOOL_CALLS = 3

    def __init__(self, database_path: str | Path, providers: ProviderRegistry | None = None, tools: ToolRegistry | None = None) -> None:
        self._path, self._providers, self._tools = Path(database_path), providers, tools
        self._dispatch_auth: dict[str, DispatchAuthorization] = {}
        self._policy = SupervisorPolicy()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS sub_agent_tasks (task_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, result_json TEXT, created_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS sub_agent_reviews (task_id TEXT PRIMARY KEY, scope TEXT NOT NULL, status TEXT NOT NULL, reviewer_session_id TEXT, note TEXT, reviewed_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS sub_agent_incorporations (incorporation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, scope TEXT NOT NULL, workspace TEXT, target_type TEXT NOT NULL, target_id TEXT NOT NULL, parent_session_id TEXT, plan_id TEXT, step_id TEXT, status TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL, UNIQUE(task_id, scope, target_type, target_id))")
            db.execute("CREATE TABLE IF NOT EXISTS supervisor_dispatch_audits (dispatch_id TEXT PRIMARY KEY, task_id TEXT, scope TEXT NOT NULL, workspace TEXT, recommendation_category TEXT NOT NULL, recommended_profile TEXT, authorized_profile TEXT, parent_session_id TEXT, plan_id TEXT, step_id TEXT, instruction_fingerprint TEXT NOT NULL, status TEXT NOT NULL, safe_reason TEXT, created_at TEXT NOT NULL, completed_at TEXT, tool_usage_json TEXT NOT NULL DEFAULT '[]')")
            columns = {row[1] for row in db.execute("PRAGMA table_info(supervisor_dispatch_audits)")}
            if "tool_usage_json" not in columns:
                db.execute("ALTER TABLE supervisor_dispatch_audits ADD COLUMN tool_usage_json TEXT NOT NULL DEFAULT '[]'")

    def create(self, request: SubAgentTaskCreate) -> SubAgentTask:
        if request.worker_profile not in PROFILES or not request.scope.strip() or not set(request.allowed_tools) <= PROFILES[request.worker_profile]:
            raise ValueError("invalid worker task")
        now = datetime.now(UTC)
        task = SubAgentTask(task_id=str(uuid4()), worker_profile=request.worker_profile, scope=request.scope, workspace=request.workspace, instruction=request.instruction, allowed_tools=sorted(set(request.allowed_tools)), status="pending", parent_session_id=request.parent_session_id, plan_id=request.plan_id, step_id=request.step_id, created_at=now)
        with sqlite3.connect(self._path) as db:
            db.execute("INSERT INTO sub_agent_tasks VALUES (?, ?, NULL, ?)", (task.task_id, task.model_dump_json(), now.isoformat()))
        return task

    def authorize_dispatch(self, request: SupervisorDispatchRequest) -> DispatchAuthorization:
        recommendation = self._policy.recommend(SupervisorRecommendationRequest(instruction=request.instruction, scope=request.scope, workspace=request.workspace))
        if recommendation.action != "delegate" or recommendation.profile != request.worker_profile:
            raise ValueError("profile does not match recommendation")
        fingerprint = hashlib.sha256(json.dumps(request.model_dump(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        capability = DispatchAuthorization(request.worker_profile, request.scope, request.workspace, request.parent_session_id, fingerprint)
        self._dispatch_auth[capability.token] = capability
        return capability

    async def dispatch(self, request: SupervisorDispatchRequest, authorization: DispatchAuthorization) -> SubAgentResult:
        expected = self._dispatch_auth.get(authorization.token)
        fingerprint = hashlib.sha256(json.dumps(request.model_dump(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if expected is not authorization or expected.profile != request.worker_profile or expected.scope != request.scope or expected.workspace != request.workspace or expected.session != request.parent_session_id or expected.fingerprint != fingerprint:
            raise ValueError("dispatch authorization denied")
        del self._dispatch_auth[authorization.token]
        recommendation = self._policy.recommend(SupervisorRecommendationRequest(instruction=request.instruction, scope=request.scope, workspace=request.workspace))
        dispatch_id = str(uuid4())
        created = datetime.now(UTC)
        with sqlite3.connect(self._path) as db:
            db.execute("INSERT INTO supervisor_dispatch_audits VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'started', NULL, ?, NULL, '[]')", (dispatch_id, request.scope, request.workspace, recommendation.category, recommendation.profile, request.worker_profile, request.parent_session_id, request.plan_id, request.step_id, authorization.fingerprint, created.isoformat()))
        task = self.create(SubAgentTaskCreate(**request.model_dump()))
        result = await self.run(task.task_id)
        with sqlite3.connect(self._path) as db:
            db.execute("UPDATE supervisor_dispatch_audits SET task_id=?, status=?, safe_reason=?, completed_at=? WHERE dispatch_id=?", (task.task_id, result.status, result.safe_reason, result.completed_at.isoformat(), dispatch_id))
        return result

    def audits(self, scope: str, limit: int = 20) -> list[SupervisorDispatchAudit]:
        if not scope.strip() or limit < 1 or limit > 100:
            raise ValueError("invalid audit query")
        with sqlite3.connect(self._path) as db:
            rows = db.execute("SELECT * FROM supervisor_dispatch_audits WHERE scope=? ORDER BY created_at DESC, dispatch_id DESC LIMIT ?", (scope, limit)).fetchall()
        return [SupervisorDispatchAudit(dispatch_id=r[0], task_id=r[1], scope=r[2], workspace=r[3], recommendation_category=r[4], recommended_profile=r[5], authorized_profile=r[6], parent_session_id=r[7], plan_id=r[8], step_id=r[9], instruction_fingerprint=r[10], status=r[11], safe_reason=r[12], created_at=datetime.fromisoformat(r[13]), completed_at=datetime.fromisoformat(r[14]) if r[14] else None, tool_usage=json.loads(r[15])) for r in rows]

    def audit(self, dispatch_id: str, scope: str) -> SupervisorDispatchAudit | None:
        return next((item for item in self.audits(scope, 100) if item.dispatch_id == dispatch_id), None)

    def record_tool_usage(self, dispatch_id: str, scope: str, tool_name: str, permission: str, status: str, result: object = None) -> None:
        audit = self.audit(dispatch_id, scope)
        if audit is None or len(audit.tool_usage) >= 10:
            raise ValueError("audit unavailable")
        event = {"sequence": len(audit.tool_usage) + 1, "tool": tool_name, "permission": permission, "status": status, "result": str(sanitize_output(result))[:500]}
        usage = [*audit.tool_usage, event]
        with sqlite3.connect(self._path) as db:
            db.execute("UPDATE supervisor_dispatch_audits SET tool_usage_json=? WHERE dispatch_id=? AND scope=?", (json.dumps(usage, sort_keys=True), dispatch_id, scope))

    def get(self, task_id: str) -> tuple[SubAgentTask, SubAgentResult | None] | None:
        with sqlite3.connect(self._path) as db:
            row = db.execute("SELECT payload_json, result_json FROM sub_agent_tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        return SubAgentTask.model_validate_json(row[0]), SubAgentResult.model_validate_json(row[1]) if row[1] else None

    async def run(self, task_id: str, provider_name: str | None = None, model: str | None = None) -> SubAgentResult:
        record = self.get(task_id)
        if record is None:
            raise ValueError("task not found")
        task, _ = record
        started = datetime.now(UTC)
        if self._providers is None or provider_name is None:
            result = SubAgentResult(task_id=task.task_id, worker_profile=task.worker_profile, status="completed", summary=task.instruction[:2000], safe_reason="deterministic_stub", started_at=started, completed_at=datetime.now(UTC))
        else:
            try:
                provider = self._providers.get(provider_name)  # type: ignore[arg-type]
                response = await provider.generate(ModelRequest(model=model or "default", messages=[ModelMessage(role="user", content=task.instruction)]))
                result = SubAgentResult(task_id=task.task_id, worker_profile=task.worker_profile, status="completed", summary=str(sanitize_output(response.content))[:4000], started_at=started, completed_at=datetime.now(UTC))
            except Exception:  # noqa: BLE001 - provider boundary returns safe failure
                result = SubAgentResult(task_id=task.task_id, worker_profile=task.worker_profile, status="failed", safe_reason="provider_error", started_at=started, completed_at=datetime.now(UTC))
        with sqlite3.connect(self._path) as db:
            db.execute("UPDATE sub_agent_tasks SET result_json=? WHERE task_id=?", (result.model_dump_json(), task_id))
            db.execute("INSERT OR IGNORE INTO sub_agent_reviews VALUES (?, ?, 'pending', NULL, NULL, ?)", (task_id, task.scope, result.completed_at.isoformat()))
        return result

    async def run_with_tools(self, task_id: str, dispatch_id: str, provider_name: str, model: str, max_calls: int = 3) -> SubAgentResult:
        if max_calls < 1 or max_calls > 5 or self._providers is None or self._tools is None:
            raise ValueError("invalid worker loop")
        record = self.get(task_id)
        if record is None:
            raise ValueError("task not found")
        task, _ = record
        provider = self._providers.get(provider_name)  # type: ignore[arg-type]
        allowed = [self._tools.get(name) for name in task.allowed_tools]
        allowed = [tool for tool in allowed if tool.permission == "read" and not tool.high_impact]
        specs = [tool for tool in self._tools.specs() if any(tool.name == item.name for item in allowed)]
        messages = [ModelMessage(role="user", content=f"Worker profile: {task.worker_profile}\nScope: {task.scope}\n{task.instruction[:4000]}")]
        calls = 0
        started = datetime.now(UTC)
        while calls < max_calls:
            response = await provider.generate(ModelRequest(model=model, messages=messages, tools=specs))
            if not response.tool_calls:
                result = SubAgentResult(task_id=task.task_id, worker_profile=task.worker_profile, status="completed", summary=str(sanitize_output(response.content))[:4000], tool_usage=[item["tool"] for item in self.audit(dispatch_id, task.scope).tool_usage] if self.audit(dispatch_id, task.scope) else [], started_at=started, completed_at=datetime.now(UTC))
                with sqlite3.connect(self._path) as db:
                    db.execute("UPDATE sub_agent_tasks SET result_json=? WHERE task_id=?", (result.model_dump_json(), task_id))
                return result
            for call in response.tool_calls:
                calls += 1
                if calls > max_calls or call.name not in {item.name for item in allowed}:
                    self.record_tool_usage(dispatch_id, task.scope, call.name, "read", "denied")
                    return SubAgentResult(task_id=task.task_id, worker_profile=task.worker_profile, status="failed", safe_reason="tool_denied_or_limit", started_at=started, completed_at=datetime.now(UTC))
                tool = self._tools.get(call.name)
                auth = self._tools.authorize(call.name, task.scope, task.parent_session_id)
                if not self._tools.validate_authorization(auth, call.name, task.scope, task.parent_session_id):
                    self.record_tool_usage(dispatch_id, task.scope, call.name, tool.permission, "denied")
                    return SubAgentResult(task_id=task.task_id, worker_profile=task.worker_profile, status="failed", safe_reason="authorization_denied", started_at=started, completed_at=datetime.now(UTC))
                try:
                    result_value = self._tools.execute(call.name, **call.arguments)
                    safe = sanitize_output(result_value)
                    self.record_tool_usage(dispatch_id, task.scope, call.name, tool.permission, "success", safe)
                except Exception:  # noqa: BLE001 - tool boundary returns safe failure
                    self.record_tool_usage(dispatch_id, task.scope, call.name, tool.permission, "error")
                    return SubAgentResult(task_id=task.task_id, worker_profile=task.worker_profile, status="failed", safe_reason="tool_error", started_at=started, completed_at=datetime.now(UTC))
                messages.extend([ModelMessage(role="assistant", content=response.content, tool_calls=[call]), ModelMessage(role="tool", content=json.dumps(safe, sort_keys=True), tool_call_id=call.id, tool_name=call.name)])
        return SubAgentResult(task_id=task.task_id, worker_profile=task.worker_profile, status="failed", safe_reason="tool_call_limit", started_at=started, completed_at=datetime.now(UTC))

    def review(self, task_id: str, scope: str, status: str, reviewer_session_id: str | None = None, note: str | None = None) -> SubAgentReview:
        record = self.get(task_id)
        if record is None or record[0].scope != scope or record[1] is None:
            raise ValueError("review not found")
        if status not in {"accepted", "rejected"} or reviewer_session_id == record[0].parent_session_id:
            raise ValueError("invalid review")
        with sqlite3.connect(self._path) as db:
            row = db.execute("SELECT status, reviewer_session_id, note, reviewed_at FROM sub_agent_reviews WHERE task_id=? AND scope=?", (task_id, scope)).fetchone()
            if row and row[0] != "pending":
                return SubAgentReview(task_id=task_id, scope=scope, status=row[0], reviewer_session_id=row[1], note=row[2], reviewed_at=datetime.fromisoformat(row[3]))
            now = datetime.now(UTC)
            bounded_note = note[:1000] if note else None
            db.execute("INSERT OR REPLACE INTO sub_agent_reviews VALUES (?, ?, ?, ?, ?, ?)", (task_id, scope, status, reviewer_session_id, bounded_note, now.isoformat()))
        return SubAgentReview(task_id=task_id, scope=scope, status=status, reviewer_session_id=reviewer_session_id, note=bounded_note, reviewed_at=now)

    def get_review(self, task_id: str, scope: str) -> SubAgentReview | None:
        with sqlite3.connect(self._path) as db:
            row = db.execute("SELECT task_id, scope, status, reviewer_session_id, note, reviewed_at FROM sub_agent_reviews WHERE task_id=? AND scope=?", (task_id, scope)).fetchone()
        return SubAgentReview(task_id=row[0], scope=row[1], status=row[2], reviewer_session_id=row[3], note=row[4], reviewed_at=datetime.fromisoformat(row[5])) if row else None

    def contribution(self, task_id: str, scope: str) -> SubAgentContribution:
        record, review = self.get(task_id) or (None, None)
        reviewed = self.get_review(task_id, scope)
        if record is None or review is None or reviewed is None or reviewed.status != "accepted" or record.scope != scope:
            raise ValueError("contribution unavailable")
        return SubAgentContribution(task_id=task_id, worker_profile=record.worker_profile, scope=scope, workspace=record.workspace, summary=review.summary[:4000], references=review.references[:20], tool_usage=review.tool_usage[:20], parent_session_id=record.parent_session_id, plan_id=record.plan_id, step_id=record.step_id, accepted_at=reviewed.reviewed_at)

    def incorporate(self, task_id: str, scope: str, target_type: str, target_id: str, note: str | None = None, reviewer_session_id: str | None = None) -> SubAgentIncorporation:
        contribution = self.contribution(task_id, scope)
        if reviewer_session_id and reviewer_session_id == contribution.parent_session_id or target_type not in {"research"} or not target_id:
            raise ValueError("invalid incorporation")
        with sqlite3.connect(self._path) as db:
            row = db.execute("SELECT * FROM sub_agent_incorporations WHERE task_id=? AND scope=? AND target_type=? AND target_id=?", (task_id, scope, target_type, target_id)).fetchone()
            if row:
                return SubAgentIncorporation(incorporation_id=row[0], task_id=row[1], scope=row[2], workspace=row[3], target_type=row[4], target_id=row[5], parent_session_id=row[6], plan_id=row[7], step_id=row[8], status=row[9], note=row[10], created_at=datetime.fromisoformat(row[11]))
            now = datetime.now(UTC)
            incorporation = SubAgentIncorporation(incorporation_id=str(uuid4()), task_id=task_id, scope=scope, workspace=contribution.workspace, target_type=target_type, target_id=target_id, parent_session_id=contribution.parent_session_id, plan_id=contribution.plan_id, step_id=contribution.step_id, status="active", note=note[:1000] if note else None, created_at=now)
            db.execute("INSERT INTO sub_agent_incorporations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (incorporation.incorporation_id, incorporation.task_id, incorporation.scope, incorporation.workspace, incorporation.target_type, incorporation.target_id, incorporation.parent_session_id, incorporation.plan_id, incorporation.step_id, incorporation.status, incorporation.note, incorporation.created_at.isoformat()))
            return incorporation

    def get_incorporation(self, incorporation_id: str, scope: str) -> SubAgentIncorporation | None:
        with sqlite3.connect(self._path) as db:
            row = db.execute("SELECT * FROM sub_agent_incorporations WHERE incorporation_id=? AND scope=?", (incorporation_id, scope)).fetchone()
        return SubAgentIncorporation(incorporation_id=row[0], task_id=row[1], scope=row[2], workspace=row[3], target_type=row[4], target_id=row[5], parent_session_id=row[6], plan_id=row[7], step_id=row[8], status=row[9], note=row[10], created_at=datetime.fromisoformat(row[11])) if row else None

    def revoke_incorporation(self, incorporation_id: str, scope: str) -> SubAgentIncorporation:
        item = self.get_incorporation(incorporation_id, scope)
        if item is None:
            raise ValueError("incorporation not found")
        with sqlite3.connect(self._path) as db:
            db.execute("UPDATE sub_agent_incorporations SET status='revoked' WHERE incorporation_id=? AND scope=?", (incorporation_id, scope))
        return item.model_copy(update={"status": "revoked"})

    def incorporated_contribution(self, incorporation_id: str, scope: str) -> SubAgentContribution:
        item = self.get_incorporation(incorporation_id, scope)
        if item is None or item.status != "active":
            raise ValueError("incorporation unavailable")
        return self.contribution(item.task_id, scope)

    def use_tool(self, task_id: str, tool_name: str, arguments: dict) -> object:
        record = self.get(task_id)
        if record is None or self._tools is None:
            raise ValueError("task not found")
        task, result = record
        if tool_name not in task.allowed_tools or result is not None:
            raise ValueError("tool not allowed")
        tool = self._tools.get(tool_name)
        if tool.high_impact or tool.permission != "read":
            raise ValueError("worker tool not allowed")
        authorization = self._tools.authorize(tool_name, task.scope, task.parent_session_id)
        if not self._tools.validate_authorization(authorization, tool_name, task.scope, task.parent_session_id):
            raise ValueError("tool permission denied")
        return sanitize_output(self._tools.execute(tool_name, **arguments))
