from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from app.context.models import VerifiedWorkflowContext
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
    cancellation_reason: str | None = None
    cancelled_at: datetime | None = None
    verifier_review_status: str | None = None
    final_outcome: str | None = None


class ResearchVerifyWorkflowResult(BaseModel):
    workflow: ResearchVerifyWorkflow
    researcher: SubAgentResult | None = None
    researcher_review_status: str | None = None
    verifier: SubAgentResult | None = None
    verifier_review_status: str | None = None
    final_outcome: str | None = None


class SupervisorResearchVerifyWorkflow:
    def __init__(self, database_path: str | Path, agents: SubAgentService) -> None:
        self._path = Path(database_path)
        self._agents = agents
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS supervisor_workflows (workflow_id TEXT PRIMARY KEY, scope TEXT NOT NULL, workspace TEXT, instruction TEXT NOT NULL, researcher_task_id TEXT, researcher_dispatch_id TEXT, researcher_review_status TEXT, verifier_task_id TEXT, verifier_dispatch_id TEXT, status TEXT NOT NULL, parent_session_id TEXT, plan_id TEXT, step_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, cancellation_reason TEXT, cancelled_at TEXT, verifier_review_status TEXT, final_outcome TEXT)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(supervisor_workflows)")}
            if "researcher_dispatch_id" not in columns:
                db.execute("ALTER TABLE supervisor_workflows ADD COLUMN researcher_dispatch_id TEXT")
            if "verifier_dispatch_id" not in columns:
                db.execute("ALTER TABLE supervisor_workflows ADD COLUMN verifier_dispatch_id TEXT")
            if "cancellation_reason" not in columns:
                db.execute("ALTER TABLE supervisor_workflows ADD COLUMN cancellation_reason TEXT")
            if "cancelled_at" not in columns:
                db.execute("ALTER TABLE supervisor_workflows ADD COLUMN cancelled_at TEXT")
            if "verifier_review_status" not in columns:
                db.execute("ALTER TABLE supervisor_workflows ADD COLUMN verifier_review_status TEXT")
            if "final_outcome" not in columns:
                db.execute("ALTER TABLE supervisor_workflows ADD COLUMN final_outcome TEXT")

    def create(self, request: ResearchVerifyWorkflowCreate) -> ResearchVerifyWorkflow:
        now = datetime.now(UTC)
        workflow = ResearchVerifyWorkflow(workflow_id=str(uuid4()), scope=request.scope, workspace=request.workspace, instruction=request.instruction, status="awaiting_research", parent_session_id=request.parent_session_id, plan_id=request.plan_id, step_id=request.step_id, created_at=now, updated_at=now)
        with sqlite3.connect(self._path) as db:
            db.execute("INSERT INTO supervisor_workflows (workflow_id, scope, workspace, instruction, status, parent_session_id, plan_id, step_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (workflow.workflow_id, workflow.scope, workflow.workspace, workflow.instruction, workflow.status, workflow.parent_session_id, workflow.plan_id, workflow.step_id, now.isoformat(), now.isoformat()))
        return workflow

    def get(self, workflow_id: str, scope: str) -> ResearchVerifyWorkflow | None:
        with sqlite3.connect(self._path) as db:
            row = db.execute(f"SELECT {self._columns()} FROM supervisor_workflows WHERE workflow_id=? AND scope=?", (workflow_id, scope)).fetchone()
        return self._model(row) if row else None

    def _model(self, row: tuple) -> ResearchVerifyWorkflow:
        return ResearchVerifyWorkflow(workflow_id=row[0], scope=row[1], workspace=row[2], instruction=row[3], researcher_task_id=row[4], researcher_dispatch_id=row[5], researcher_review_status=row[6], verifier_task_id=row[7], verifier_dispatch_id=row[8], status=row[9], parent_session_id=row[10], plan_id=row[11], step_id=row[12], created_at=datetime.fromisoformat(row[13]), updated_at=datetime.fromisoformat(row[14]), cancellation_reason=row[15], cancelled_at=datetime.fromisoformat(row[16]) if row[16] else None, verifier_review_status=row[17], final_outcome=row[18])

    def list(self, scope: str, limit: int = 20, status: str | None = None) -> list[ResearchVerifyWorkflow]:
        if not scope.strip() or limit < 1 or limit > 100 or status not in {None, "awaiting_research", "awaiting_review", "awaiting_verifier_review", "completed", "failed", "cancelled"}:
            raise ValueError("invalid workflow query")
        query = f"SELECT {self._columns()} FROM supervisor_workflows WHERE scope=?"
        values: list[object] = [scope]
        if status:
            query += " AND status=?"
            values.append(status)
        query += " ORDER BY created_at DESC, workflow_id DESC LIMIT ?"
        values.append(limit)
        with sqlite3.connect(self._path) as db:
            rows = db.execute(query, values).fetchall()
        return [self._model(row) for row in rows]

    @staticmethod
    def _columns() -> str:
        return "workflow_id, scope, workspace, instruction, researcher_task_id, researcher_dispatch_id, researcher_review_status, verifier_task_id, verifier_dispatch_id, status, parent_session_id, plan_id, step_id, created_at, updated_at, cancellation_reason, cancelled_at, verifier_review_status, final_outcome"

    async def start_research(self, workflow_id: str, scope: str) -> ResearchVerifyWorkflowResult:
        workflow = self.get(workflow_id, scope)
        if workflow is None:
            raise ValueError("workflow not found")
        if workflow.status in {"completed", "failed", "cancelled"}:
            raise ValueError("workflow is terminal")
        if workflow.researcher_task_id:
            return self.result(workflow)
        if workflow.status != "awaiting_research":
            raise ValueError("research unavailable")
        request = SupervisorDispatchRequest(worker_profile="researcher", scope=scope, workspace=workflow.workspace, instruction=f"Research: {workflow.instruction}", parent_session_id=workflow.parent_session_id, plan_id=workflow.plan_id, step_id=workflow.step_id, **(self._agents.workflow_options("researcher") if getattr(self._agents, "provider_backed", False) else {}))
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
        if workflow.status in {"completed", "failed", "cancelled"}:
            raise ValueError("workflow is terminal")
        review = self._agents.get_review(workflow.researcher_task_id, scope)
        if review is None or review.status != "accepted":
            raise ValueError("research review required")
        if workflow.verifier_task_id:
            return self.result(workflow)
        if workflow.status != "awaiting_review":
            raise ValueError("verification unavailable")
        contribution = self._agents.contribution(workflow.researcher_task_id, scope)
        request = SupervisorDispatchRequest(worker_profile="verifier", scope=scope, workspace=workflow.workspace, instruction=f"Verify completed work independently. Original goal: {workflow.instruction[:1800]}\nAccepted analysis: {contribution.summary[:2000]}", parent_session_id=workflow.parent_session_id, plan_id=workflow.plan_id, step_id=workflow.step_id, **(self._agents.workflow_options("verifier") if getattr(self._agents, "provider_backed", False) else {}))
        authorization = self._agents.authorize_dispatch(request)
        try:
            result = await self._agents.dispatch(request, authorization)
        except ValueError:
            self._update(workflow_id, scope, status="failed")
            raise ValueError("verification dispatch failed")
        dispatch_id = next((audit.dispatch_id for audit in self._agents.audits(scope, 100) if audit.task_id == result.task_id), None)
        self._update(workflow_id, scope, verifier_task_id=result.task_id, verifier_dispatch_id=dispatch_id, verifier_review_status="pending", status="awaiting_verifier_review" if result.status == "completed" else "failed")
        return self.result(self.get(workflow_id, scope))

    def review_status(self, workflow_id: str, scope: str) -> str | None:
        workflow = self.get(workflow_id, scope)
        if workflow is None or not workflow.researcher_task_id:
            return None
        review = self._agents.get_review(workflow.researcher_task_id, scope)
        if review:
            self._update(workflow_id, scope, researcher_review_status=review.status)
        return review.status if review else None

    def review_research(self, workflow_id: str, scope: str, status: str, reviewer_session_id: str | None = None, note: str | None = None) -> ResearchVerifyWorkflowResult:
        workflow = self.get(workflow_id, scope)
        if workflow is None or not workflow.researcher_task_id or workflow.status != "awaiting_review" or workflow.researcher_review_status in {"accepted", "rejected"}:
            raise ValueError("research review unavailable")
        if status not in {"accepted", "rejected"}:
            raise ValueError("invalid research review")
        review = self._agents.review(workflow.researcher_task_id, scope, status, reviewer_session_id, note)
        self._update(workflow_id, scope, researcher_review_status=review.status, status="awaiting_review" if status == "accepted" else "failed", final_outcome=None if status == "accepted" else "rejected")
        return self.result(self.get(workflow_id, scope))

    def cancel(self, workflow_id: str, scope: str, reason: str | None = None) -> ResearchVerifyWorkflow:
        workflow = self.get(workflow_id, scope)
        if workflow is None:
            raise ValueError("workflow not found")
        if workflow.status == "cancelled":
            return workflow
        if workflow.status in {"completed", "failed"}:
            raise ValueError("workflow is terminal")
        now = datetime.now(UTC)
        bounded = reason[:500] if reason else None
        with sqlite3.connect(self._path) as db:
            db.execute("UPDATE supervisor_workflows SET status='cancelled', cancellation_reason=?, cancelled_at=?, updated_at=? WHERE workflow_id=? AND scope=?", (bounded, now.isoformat(), now.isoformat(), workflow_id, scope))
        return self.get(workflow_id, scope)  # type: ignore[return-value]

    def review_verifier(self, workflow_id: str, scope: str, status: str, reviewer_session_id: str | None = None, note: str | None = None) -> ResearchVerifyWorkflowResult:
        workflow = self.get(workflow_id, scope)
        if workflow is None or not workflow.verifier_task_id:
            raise ValueError("verifier review unavailable")
        if workflow.status in {"completed", "failed", "cancelled"}:
            raise ValueError("workflow is terminal")
        review = self._agents.review(workflow.verifier_task_id, scope, status, reviewer_session_id, note)
        outcome = "verified" if status == "accepted" else "rejected"
        self._update(workflow_id, scope, verifier_review_status=review.status, final_outcome=outcome, status="completed" if status == "accepted" else "failed")
        return self.result(self.get(workflow_id, scope))

    def verified_context(self, workflow_id: str, scope: str, workspace: str | None = None) -> VerifiedWorkflowContext:
        workflow = self.get(workflow_id, scope)
        if workflow is None or workflow.status != "completed" or workflow.final_outcome != "verified" or (workspace is not None and workflow.workspace != workspace):
            raise ValueError("verified workflow unavailable")
        researcher = self._agents.get(workflow.researcher_task_id)[1] if workflow.researcher_task_id and self._agents.get(workflow.researcher_task_id) else None
        verifier = self._agents.get(workflow.verifier_task_id)[1] if workflow.verifier_task_id and self._agents.get(workflow.verifier_task_id) else None
        if researcher is None or verifier is None:
            raise ValueError("verified workflow unavailable")
        return VerifiedWorkflowContext(workflow_id=workflow.workflow_id, scope=scope, workspace=workflow.workspace, researcher_summary=researcher.summary[:4000], verifier_summary=verifier.summary[:4000], researcher_task_id=workflow.researcher_task_id, verifier_task_id=workflow.verifier_task_id)

    def _update(self, workflow_id: str, scope: str, **changes: str | None) -> None:
        workflow = self.get(workflow_id, scope)
        if workflow is None:
            raise ValueError("workflow not found")
        values = {field: getattr(workflow, field) for field in ("researcher_task_id", "researcher_dispatch_id", "researcher_review_status", "verifier_task_id", "verifier_dispatch_id", "status", "cancellation_reason", "cancelled_at", "verifier_review_status", "final_outcome")}
        values.update(changes)
        now = datetime.now(UTC)
        with sqlite3.connect(self._path) as db:
            db.execute("UPDATE supervisor_workflows SET researcher_task_id=?, researcher_dispatch_id=?, researcher_review_status=?, verifier_task_id=?, verifier_dispatch_id=?, status=?, cancellation_reason=?, cancelled_at=?, verifier_review_status=?, final_outcome=?, updated_at=? WHERE workflow_id=? AND scope=?", (values["researcher_task_id"], values["researcher_dispatch_id"], values["researcher_review_status"], values["verifier_task_id"], values["verifier_dispatch_id"], values["status"], values["cancellation_reason"], values["cancelled_at"], values["verifier_review_status"], values["final_outcome"], now.isoformat(), workflow_id, scope))

    def result(self, workflow: ResearchVerifyWorkflow | None) -> ResearchVerifyWorkflowResult:
        if workflow is None:
            raise ValueError("workflow not found")
        review_status = self.review_status(workflow.workflow_id, workflow.scope)
        workflow = self.get(workflow.workflow_id, workflow.scope) or workflow
        researcher = self._agents.get(workflow.researcher_task_id)[1] if workflow.researcher_task_id and self._agents.get(workflow.researcher_task_id) else None
        verifier = self._agents.get(workflow.verifier_task_id)[1] if workflow.verifier_task_id and self._agents.get(workflow.verifier_task_id) else None
        return ResearchVerifyWorkflowResult(workflow=workflow, researcher=researcher, researcher_review_status=review_status, verifier=verifier, verifier_review_status=workflow.verifier_review_status, final_outcome=workflow.final_outcome)
