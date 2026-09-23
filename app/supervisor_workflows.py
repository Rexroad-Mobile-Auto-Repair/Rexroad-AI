from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from app.subagents import SubAgentResult, SubAgentService, SupervisorDispatchRequest


class ResearchVerifyWorkflowCreate(BaseModel):
    scope: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=4000)
    workspace: str | None = None
    parent_session_id: str | None = None
    plan_id: str | None = None
    step_id: str | None = None


class ResearchVerifyWorkflow(BaseModel):
    workflow_id: str
    scope: str
    workspace: str | None
    instruction: str
    researcher_task_id: str | None = None
    researcher_dispatch_id: str | None = None
    researcher_review_status: str | None = None
    verifier_task_id: str | None = None
    verifier_dispatch_id: str | None = None
    status: str
    parent_session_id: str | None
    plan_id: str | None
    step_id: str | None
    created_at: datetime
    updated_at: datetime


class ResearchVerifyWorkflowResult(BaseModel):
    workflow: ResearchVerifyWorkflow
    researcher: SubAgentResult | None = None
    researcher_review_status: str | None = None
    verifier: SubAgentResult | None = None


class SupervisorResearchVerifyWorkflow:
    def __init__(self, database_path: str | Path, agents: SubAgentService) -> None:
        self._path = Path(database_path)
        self._agents = agents
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS supervisor_workflows (workflow_id TEXT PRIMARY KEY, scope TEXT NOT NULL, workspace TEXT, instruction TEXT NOT NULL, researcher_task_id TEXT, researcher_dispatch_id TEXT, researcher_review_status TEXT, verifier_task_id TEXT, verifier_dispatch_id TEXT, status TEXT NOT NULL, parent_session_id TEXT, plan_id TEXT, step_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")

    def create(self, request: ResearchVerifyWorkflowCreate) -> ResearchVerifyWorkflow:
        now = datetime.now(UTC)
        workflow = ResearchVerifyWorkflow(workflow_id=str(uuid4()), scope=request.scope, workspace=request.workspace, instruction=request.instruction, status="awaiting_research", parent_session_id=request.parent_session_id, plan_id=request.plan_id, step_id=request.step_id, created_at=now, updated_at=now)
        with sqlite3.connect(self._path) as db:
            db.execute("INSERT INTO supervisor_workflows VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?)", (workflow.workflow_id, workflow.scope, workflow.workspace, workflow.instruction, workflow.status, workflow.parent_session_id, workflow.plan_id, workflow.step_id, now.isoformat(), now.isoformat()))
        return workflow

    def get(self, workflow_id: str, scope: str) -> ResearchVerifyWorkflow | None:
        with sqlite3.connect(self._path) as db:
            row = db.execute("SELECT * FROM supervisor_workflows WHERE workflow_id=? AND scope=?", (workflow_id, scope)).fetchone()
        return self._model(row) if row else None

    def _model(self, row: tuple) -> ResearchVerifyWorkflow:
        return ResearchVerifyWorkflow(workflow_id=row[0], scope=row[1], workspace=row[2], instruction=row[3], researcher_task_id=row[4], researcher_dispatch_id=row[5], researcher_review_status=row[6], verifier_task_id=row[7], verifier_dispatch_id=row[8], status=row[9], parent_session_id=row[10], plan_id=row[11], step_id=row[12], created_at=datetime.fromisoformat(row[13]), updated_at=datetime.fromisoformat(row[14]))

    async def start_research(self, workflow_id: str, scope: str) -> ResearchVerifyWorkflowResult:
        workflow = self.get(workflow_id, scope)
        if workflow is None:
            raise ValueError("workflow not found")
        if workflow.researcher_task_id:
            return self.result(workflow)
        if workflow.status != "awaiting_research":
            raise ValueError("research unavailable")
        request = SupervisorDispatchRequest(worker_profile="researcher", scope=scope, workspace=workflow.workspace, instruction=f"Research: {workflow.instruction}", parent_session_id=workflow.parent_session_id, plan_id=workflow.plan_id, step_id=workflow.step_id)
        authorization = self._agents.authorize_dispatch(request)
        try:
            result = await self._agents.dispatch(request, authorization)
        except ValueError:
            self._update(workflow_id, scope, status="failed")
            raise ValueError("research dispatch failed")
        task_id = result.task_id
        status = "awaiting_review" if result.status == "completed" else "failed"
        dispatch_id = next((audit.dispatch_id for audit in self._agents.audits(scope, 100) if audit.task_id == task_id), None)
        self._update(workflow_id, scope, researcher_task_id=task_id, researcher_dispatch_id=dispatch_id, status=status)
        return self.result(self.get(workflow_id, scope))

    async def start_verification(self, workflow_id: str, scope: str) -> ResearchVerifyWorkflowResult:
        workflow = self.get(workflow_id, scope)
        if workflow is None or not workflow.researcher_task_id:
            raise ValueError("workflow not found")
        review = self._agents.get_review(workflow.researcher_task_id, scope)
        if review is None or review.status != "accepted":
            raise ValueError("research review required")
        if workflow.verifier_task_id:
            return self.result(workflow)
        if workflow.status != "awaiting_review":
            raise ValueError("verification unavailable")
        contribution = self._agents.contribution(workflow.researcher_task_id, scope)
        request = SupervisorDispatchRequest(worker_profile="verifier", scope=scope, workspace=workflow.workspace, instruction=f"Verify completed work independently. Original goal: {workflow.instruction[:1800]}\nAccepted analysis: {contribution.summary[:2000]}", parent_session_id=workflow.parent_session_id, plan_id=workflow.plan_id, step_id=workflow.step_id)
        authorization = self._agents.authorize_dispatch(request)
        try:
            result = await self._agents.dispatch(request, authorization)
        except ValueError:
            self._update(workflow_id, scope, status="failed")
            raise ValueError("verification dispatch failed")
        dispatch_id = next((audit.dispatch_id for audit in self._agents.audits(scope, 100) if audit.task_id == result.task_id), None)
        self._update(workflow_id, scope, verifier_task_id=result.task_id, verifier_dispatch_id=dispatch_id, status="completed" if result.status == "completed" else "failed")
        return self.result(self.get(workflow_id, scope))

    def review_status(self, workflow_id: str, scope: str) -> str | None:
        workflow = self.get(workflow_id, scope)
        if workflow is None or not workflow.researcher_task_id:
            return None
        review = self._agents.get_review(workflow.researcher_task_id, scope)
        if review:
            self._update(workflow_id, scope, researcher_review_status=review.status)
        return review.status if review else None

    def _update(self, workflow_id: str, scope: str, **changes: str | None) -> None:
        workflow = self.get(workflow_id, scope)
        if workflow is None:
            raise ValueError("workflow not found")
        values = {field: getattr(workflow, field) for field in ("researcher_task_id", "researcher_dispatch_id", "researcher_review_status", "verifier_task_id", "verifier_dispatch_id", "status")}
        values.update(changes)
        now = datetime.now(UTC)
        with sqlite3.connect(self._path) as db:
            db.execute("UPDATE supervisor_workflows SET researcher_task_id=?, researcher_dispatch_id=?, researcher_review_status=?, verifier_task_id=?, verifier_dispatch_id=?, status=?, updated_at=? WHERE workflow_id=? AND scope=?", (values["researcher_task_id"], values["researcher_dispatch_id"], values["researcher_review_status"], values["verifier_task_id"], values["verifier_dispatch_id"], values["status"], now.isoformat(), workflow_id, scope))

    def result(self, workflow: ResearchVerifyWorkflow | None) -> ResearchVerifyWorkflowResult:
        if workflow is None:
            raise ValueError("workflow not found")
        review_status = self.review_status(workflow.workflow_id, workflow.scope)
        workflow = self.get(workflow.workflow_id, workflow.scope) or workflow
        researcher = self._agents.get(workflow.researcher_task_id)[1] if workflow.researcher_task_id and self._agents.get(workflow.researcher_task_id) else None
        verifier = self._agents.get(workflow.verifier_task_id)[1] if workflow.verifier_task_id and self._agents.get(workflow.verifier_task_id) else None
        return ResearchVerifyWorkflowResult(workflow=workflow, researcher=researcher, researcher_review_status=review_status, verifier=verifier)
