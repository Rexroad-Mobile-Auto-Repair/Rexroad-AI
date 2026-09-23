from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from pydantic import BaseModel, Field

from app.coding_workflows import CodingWorkflowService
from app.policy.workspaces import WorkspaceRegistry
from app.tools.git import ReadOnlyGit


class ProposedChange(BaseModel):
    relative_path: str
    operation: str = "replace"
    expected_text: str = Field(min_length=1, max_length=65536)
    replacement: str = Field(max_length=65536)
    reason: str = Field(default="", max_length=1000)


class ProposedCheck(BaseModel):
    check_id: str
    targets: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(default="", max_length=500)


class ProposalCreate(BaseModel):
    changes: list[ProposedChange] = Field(min_length=1, max_length=5)
    checks: list[ProposedCheck] = Field(default_factory=list, max_length=5)
    summary: str = Field(default="", max_length=2000)


class CodingProposal(BaseModel):
    proposal_id: str
    workflow_id: str
    analyst_task_id: str
    scope: str
    workspace: str
    objective: str
    status: str
    changes: list[ProposedChange]
    checks: list[ProposedCheck]
    summary: str
    baseline_head: str
    target_hashes: dict[str, str]
    created_at: datetime
    updated_at: datetime


class CodingProposalService:
    ALLOWED_CHECKS: ClassVar[set[str]] = {"pytest", "ruff", "git_diff_check"}

    def __init__(self, database_path: str | Path, workflows: CodingWorkflowService, workspaces: WorkspaceRegistry, git: ReadOnlyGit) -> None:
        self.path = Path(database_path); self.workflows = workflows; self.workspaces = workspaces; self.git = git
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS coding_proposals (proposal_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)")

    def create(self, workflow_id: str, scope: str, request: ProposalCreate) -> CodingProposal:
        workflow = self.workflows.get(workflow_id, scope)
        if workflow is None or workflow.status != "awaiting_analysis_review" or not workflow.analyst_task_id:
            raise ValueError("accepted analyst review required")
        review = self.workflows.agents.get_review(workflow.analyst_task_id, scope)
        if review is None or review.status != "accepted": raise ValueError("accepted analyst review required")
        self.workspaces.get_root(workflow.workspace)
        status = self.git.status(workflow.workspace)
        dirty = {line[3:].strip().split(" -> ")[-1] for line in status.splitlines() if line and not line.startswith("##")}
        hashes: dict[str, str] = {}
        for change in request.changes:
            if change.operation != "replace": raise ValueError("unsupported operation")
            if change.relative_path in dirty: raise ValueError("target is pre-existing dirty")
            if not change.relative_path or Path(change.relative_path).is_absolute() or ".." in Path(change.relative_path).parts:
                raise ValueError("invalid workspace path")
            path = self.workspaces.resolve_path(workflow.workspace, change.relative_path)
            if not path.is_file(): raise ValueError("target file not found")
            hashes[change.relative_path] = hashlib.sha256(path.read_bytes()).hexdigest()
        for check in request.checks:
            if check.check_id not in self.ALLOWED_CHECKS: raise ValueError("unsupported check")
            for target in check.targets:
                resolved = self.workspaces.resolve_path(workflow.workspace, target)
                if not resolved.is_file(): raise ValueError("check target not found")
        now = datetime.now(UTC)
        proposal = CodingProposal(proposal_id=str(uuid4()), workflow_id=workflow_id, analyst_task_id=workflow.analyst_task_id, scope=scope, workspace=workflow.workspace, objective=workflow.instruction, status="ready_for_review", changes=request.changes, checks=request.checks, summary=request.summary, baseline_head=self.git.show(workflow.workspace, "HEAD")[:80], target_hashes=hashes, created_at=now, updated_at=now)
        self._save(proposal); return proposal

    def get(self, proposal_id: str, scope: str) -> CodingProposal | None:
        with sqlite3.connect(self.path) as db: row = db.execute("SELECT payload_json FROM coding_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
        if not row: return None
        item = CodingProposal.model_validate_json(row[0]); return item if item.scope == scope else None

    def review(self, proposal_id: str, scope: str, status: str, reviewer_session_id: str | None = None) -> CodingProposal:
        item = self.get(proposal_id, scope)
        if item is None or status not in {"accepted", "rejected"}: raise ValueError("proposal unavailable")
        if reviewer_session_id and reviewer_session_id == item.analyst_task_id: raise ValueError("worker cannot review proposal")
        if item.status not in {"ready_for_review", "accepted"}: return item
        updated = item.model_copy(update={"status": status, "updated_at": datetime.now(UTC)})
        self._save(updated); return updated

    def _save(self, item: CodingProposal) -> None:
        with sqlite3.connect(self.path) as db: db.execute("INSERT OR REPLACE INTO coding_proposals VALUES (?, ?)", (item.proposal_id, item.model_dump_json()))
