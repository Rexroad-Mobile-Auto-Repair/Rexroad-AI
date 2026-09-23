from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from app.providers.models import ModelMessage, ModelRequest
from app.providers.registry import ProviderRegistry
from app.tools.output_policy import sanitize_output

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


class SubAgentService:
    def __init__(self, database_path: str | Path, providers: ProviderRegistry | None = None) -> None:
        self._path, self._providers = Path(database_path), providers
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS sub_agent_tasks (task_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, result_json TEXT, created_at TEXT NOT NULL)")

    def create(self, request: SubAgentTaskCreate) -> SubAgentTask:
        if request.worker_profile not in PROFILES or not request.scope.strip() or not set(request.allowed_tools) <= PROFILES[request.worker_profile]:
            raise ValueError("invalid worker task")
        now = datetime.now(UTC)
        task = SubAgentTask(task_id=str(uuid4()), worker_profile=request.worker_profile, scope=request.scope, workspace=request.workspace, instruction=request.instruction, allowed_tools=sorted(set(request.allowed_tools)), status="pending", parent_session_id=request.parent_session_id, plan_id=request.plan_id, step_id=request.step_id, created_at=now)
        with sqlite3.connect(self._path) as db:
            db.execute("INSERT INTO sub_agent_tasks VALUES (?, ?, NULL, ?)", (task.task_id, task.model_dump_json(), now.isoformat()))
        return task

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
        return result
