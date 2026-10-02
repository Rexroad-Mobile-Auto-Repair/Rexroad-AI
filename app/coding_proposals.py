from __future__ import annotations

import difflib
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from pydantic import BaseModel, Field

from app.coding_workflows import CodingWorkflowService
from app.plans.models import PlanCreate, PlanStepCreate
from app.policy.workspaces import WorkspaceRegistry
from app.storage import SQLiteDatabase
from app.structured_output import StructuredOutputError, StructuredOutputService
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


class RevisionCandidate(BaseModel):
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
    patch_spec_ids: list[str] = Field(default_factory=list)
    check_spec_ids: list[str] = Field(default_factory=list)
    conversion_status: str | None = None
    converted_at: datetime | None = None
    spec_review_status: str | None = None
    spec_reviewed_at: datetime | None = None
    revision_number: int = 1
    parent_proposal_id: str | None = None
    superseded_by_proposal_id: str | None = None
    revision_note: str | None = None


class CodingProposalService:
    ALLOWED_CHECKS: ClassVar[set[str]] = {"pytest", "ruff", "git_diff_check"}

    def __init__(self, database_path: str | Path, workflows: CodingWorkflowService, workspaces: WorkspaceRegistry, git: ReadOnlyGit, failure_injector=None) -> None:
        self.path = Path(database_path); self.workflows = workflows; self.workspaces = workspaces; self.git = git
        self.failure_injector = failure_injector
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS coding_proposals (proposal_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS proposal_revision_handoffs (task_id TEXT PRIMARY KEY, scope TEXT NOT NULL, workflow_id TEXT NOT NULL, parent_proposal_id TEXT NOT NULL, note TEXT NOT NULL, materialized_proposal_id TEXT)")

    def create(self, workflow_id: str, scope: str, request: ProposalCreate) -> CodingProposal:
        proposal = self._build_proposal(workflow_id, scope, request)
        self._save(proposal)
        return proposal

    def create_with_connection(self, connection: sqlite3.Connection, workflow_id: str, scope: str, request: ProposalCreate, *, parent_proposal_id: str | None = None, revision_number: int = 1, revision_note: str | None = None) -> CodingProposal:
        proposal = self._build_proposal(workflow_id, scope, request).model_copy(update={"parent_proposal_id": parent_proposal_id, "revision_number": revision_number, "revision_note": revision_note})
        self.save_with_connection(connection, proposal)
        self._fail("child_insert")
        return proposal

    def _build_proposal(self, workflow_id: str, scope: str, request: ProposalCreate) -> CodingProposal:
        workflow = self.workflows.get(workflow_id, scope)
        if workflow is None or workflow.status not in {"awaiting_analysis_review", "analysis_accepted"} or not workflow.analyst_task_id:
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
            if change.expected_text not in path.read_text(encoding="utf-8", errors="replace"): raise ValueError("expected text no longer matches")
            hashes[change.relative_path] = hashlib.sha256(path.read_bytes()).hexdigest()
        for check in request.checks:
            if check.check_id not in self.ALLOWED_CHECKS: raise ValueError("unsupported check")
            for target in check.targets:
                resolved = self.workspaces.resolve_path(workflow.workspace, target)
                if not resolved.is_file(): raise ValueError("check target not found")
        now = datetime.now(UTC)
        proposal = CodingProposal(proposal_id=str(uuid4()), workflow_id=workflow_id, analyst_task_id=workflow.analyst_task_id, scope=scope, workspace=workflow.workspace, objective=workflow.instruction, status="ready_for_review", changes=request.changes, checks=request.checks, summary=request.summary, baseline_head=self.git.show(workflow.workspace, "HEAD")[:80], target_hashes=hashes, created_at=now, updated_at=now)
        return proposal

    def convert(self, proposal_id: str, scope: str, workspace: str) -> CodingProposal:
        proposal = self.get(proposal_id, scope)
        if proposal is None or proposal.workspace != workspace: raise ValueError("proposal not found")
        if proposal.status != "accepted": raise ValueError("accepted proposal required")
        if proposal.conversion_status == "converted": return proposal
        self.workspaces.get_root(workspace)
        status = self.git.status(workspace)
        dirty = {line[3:].strip().split(" -> ")[-1] for line in status.splitlines() if line and not line.startswith("##")}
        for change in proposal.changes:
            if change.relative_path in dirty: raise ValueError("target is pre-existing dirty")
            path = self.workspaces.resolve_path(workspace, change.relative_path)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != proposal.target_hashes.get(change.relative_path): raise ValueError("proposal is stale")
            if change.expected_text not in path.read_text(encoding="utf-8", errors="replace"): raise ValueError("expected text no longer matches")
        for check in proposal.checks:
            if check.check_id not in self.ALLOWED_CHECKS: raise ValueError("unsupported check")
        workflow = self.workflows.get(proposal.workflow_id, scope)
        if workflow is None:
            raise ValueError("workflow not found")
        session_id = workflow.parent_session_id or f"coding:{proposal.workflow_id}"
        # The mutation tool compares the whole file and rejects dirty targets.
        # Combine all approved snippets for a file into one guarded write.
        originals: dict[str, str] = {}
        replacements: dict[str, str] = {}
        for change in proposal.changes:
            name = change.relative_path
            if name not in originals:
                originals[name] = self.workspaces.resolve_path(workspace, name).read_text(encoding="utf-8")
                replacements[name] = originals[name]
            if replacements[name].count(change.expected_text) != 1:
                raise ValueError("patch anchor is ambiguous or conflicts with another change")
            replacements[name] = replacements[name].replace(change.expected_text, change.replacement, 1)
        patches = [ProposedChange(relative_path=name, expected_text=text, replacement=replacements[name])
                   for name, text in originals.items()]
        if any(len(value.encode("utf-8")) > 64_000 for patch in patches
               for value in (patch.expected_text, patch.replacement)):
            raise ValueError("patch payload exceeds limit")
        steps = [PlanStepCreate(title=f"Apply {c.relative_path}", metadata={"proposal_id": proposal_id}) for c in patches] + [PlanStepCreate(title=f"Run {c.check_id}", metadata={"proposal_id": proposal_id}) for c in proposal.checks]
        patch_ids: list[str] = []; check_ids: list[str] = []
        updated = proposal.model_copy(update={"patch_spec_ids": patch_ids, "check_spec_ids": check_ids, "conversion_status": "converted", "spec_review_status": "pending", "converted_at": datetime.now(UTC), "updated_at": datetime.now(UTC)})
        # Keep lightweight test doubles and older integrations compatible; the
        # real services always take the atomic connection-aware path below.
        if not hasattr(self.workflows.plans, "create_with_connection"):
            plan = self.workflows.plans.create(PlanCreate(scope=scope, workspace=workspace, goal=proposal.objective, steps=steps))
            for step, change in zip(plan.steps, patches):
                patch_ids.append(self.workflows.specs.create(scope=scope, plan_id=plan.id, step_id=step.id, tool_name="filesystem.apply_patch", arguments={"workspace": workspace, "relative_path": change.relative_path, "expected_text": change.expected_text, "replacement": change.replacement}, verification={"type": "result_present"}, session_id=session_id).id)
            for step, check in zip(plan.steps[len(patches):], proposal.checks):
                check_ids.append(self.workflows.specs.create(scope=scope, plan_id=plan.id, step_id=step.id, tool_name="workspace.run_check", arguments={"workspace": workspace, "check_id": check.check_id, "targets": check.targets}, verification={"type": "field_equals", "field": "passed", "expected": True}, session_id=session_id).id)
            return updated.model_copy(update={"patch_spec_ids": patch_ids, "check_spec_ids": check_ids})
        with SQLiteDatabase(self.path).transaction(immediate=True) as connection:
            plan = self.workflows.plans.create_with_connection(connection, PlanCreate(scope=scope, workspace=workspace, goal=proposal.objective, steps=steps))
            self._fail("plan_insert")
            for index, (step, change) in enumerate(zip(plan.steps, patches), 1):
                spec = self.workflows.specs.create_with_connection(connection, scope=scope, plan_id=plan.id, step_id=step.id, tool_name="filesystem.apply_patch", arguments={"workspace": workspace, "relative_path": change.relative_path, "expected_text": change.expected_text, "replacement": change.replacement}, verification={"type": "result_present"}, session_id=session_id, validate_step=False)
                patch_ids.append(spec.id); self._fail(f"patch_spec_insert_{index}")
            for index, (step, check) in enumerate(zip(plan.steps[len(patches):], proposal.checks), 1):
                spec = self.workflows.specs.create_with_connection(connection, scope=scope, plan_id=plan.id, step_id=step.id, tool_name="workspace.run_check", arguments={"workspace": workspace, "check_id": check.check_id, "targets": check.targets}, verification={"type": "field_equals", "field": "passed", "expected": True}, session_id=session_id, validate_step=False)
                check_ids.append(spec.id); self._fail(f"check_spec_insert_{index}")
            updated = proposal.model_copy(update={"patch_spec_ids": patch_ids, "check_spec_ids": check_ids, "conversion_status": "converted", "spec_review_status": "pending", "converted_at": datetime.now(UTC), "updated_at": datetime.now(UTC)})
            self._fail("proposal_metadata")
            CodingProposalService.save_with_connection(connection, updated)
        return updated

    def spec_review(self, proposal_id: str, scope: str) -> dict:
        proposal = self.get(proposal_id, scope)
        if proposal is None or proposal.conversion_status != "converted": raise ValueError("proposal specs unavailable")
        # Older saved proposals can still have one spec per snippet.
        changes = proposal.changes
        if len(proposal.patch_spec_ids) != len(changes):
            names = list(dict.fromkeys(c.relative_path for c in changes))
            changes = [ProposedChange(relative_path=name,
                       expected_text="\n".join(c.expected_text for c in proposal.changes if c.relative_path == name),
                       replacement="\n".join(c.replacement for c in proposal.changes if c.relative_path == name))
                       for name in names]
        return {"proposal_id": proposal.proposal_id, "workflow_id": proposal.workflow_id, "scope": proposal.scope, "workspace": proposal.workspace, "status": proposal.spec_review_status, "patch_specs": [{"spec_id": sid, "tool": "filesystem.apply_patch", "relative_path": c.relative_path, "expected_preview": c.expected_text[:500], "replacement_preview": c.replacement[:500], "expected_hash": proposal.target_hashes.get(c.relative_path)} for sid, c in zip(proposal.patch_spec_ids, changes)], "check_specs": [{"spec_id": sid, "tool": "workspace.run_check", "check_id": c.check_id, "targets": c.targets} for sid, c in zip(proposal.check_spec_ids, proposal.checks)]}

    def preview(self, proposal_id: str, scope: str) -> dict:
        proposal = self.get(proposal_id, scope)
        if proposal is None:
            raise ValueError("proposal not found")
        previews = []
        total = 0
        for change in proposal.changes:
            path = self.workspaces.resolve_path(proposal.workspace, change.relative_path)
            current_hash = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            dirty = any(line[3:].strip().split(" -> ")[-1] == change.relative_path for line in self.git.status(proposal.workspace).splitlines() if line and not line.startswith("##"))
            stale = current_hash != proposal.target_hashes.get(change.relative_path) or not path.is_file() or change.expected_text not in (path.read_text(encoding="utf-8", errors="replace") if path.is_file() else "")
            before = change.expected_text[:2000]
            after = change.replacement[:2000]
            diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=f"a/{change.relative_path}", tofile=f"b/{change.relative_path}"))
            truncated = len(diff) > 4000
            diff = diff[:4000]
            total += len(diff)
            previews.append({"relative_path": change.relative_path, "operation": change.operation, "before_preview": before, "after_preview": after, "unified_diff": diff, "expected_hash": proposal.target_hashes.get(change.relative_path), "reason": change.reason, "stale": stale, "unsafe": dirty, "truncated": truncated})
        return {"workflow_id": proposal.workflow_id, "proposal_id": proposal.proposal_id, "revision_number": proposal.revision_number, "parent_proposal_id": proposal.parent_proposal_id, "status": proposal.status, "scope": proposal.scope, "workspace": proposal.workspace, "objective": proposal.objective, "revision_note": proposal.revision_note, "changes": previews, "checks": [c.model_dump() for c in proposal.checks], "truncated": total > 12000}

    def history(self, workflow_id: str, scope: str, limit: int = 5) -> list[dict]:
        if limit < 1 or limit > 5:
            raise ValueError("invalid limit")
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT payload_json FROM coding_proposals").fetchall()
        items = [CodingProposal.model_validate_json(row[0]) for row in rows]
        return [{"proposal_id": item.proposal_id, "revision_number": item.revision_number, "parent_proposal_id": item.parent_proposal_id, "superseded_by_proposal_id": item.superseded_by_proposal_id, "status": item.status, "created_at": item.created_at, "updated_at": item.updated_at, "revision_note": item.revision_note, "conversion_status": item.conversion_status} for item in sorted((i for i in items if i.workflow_id == workflow_id and i.scope == scope), key=lambda i: (i.revision_number, i.created_at, i.proposal_id))[:limit]]

    async def request_revision(self, proposal_id: str, scope: str, note: str):
        proposal = self.get(proposal_id, scope)
        if proposal is None or proposal.status != "rejected" or proposal.conversion_status == "converted":
            raise ValueError("only rejected unconverted proposals can be revised")
        history = self.history(proposal.workflow_id, scope, 5)
        if proposal.revision_number != max(item["revision_number"] for item in history) or proposal.revision_number >= 5:
            raise ValueError("revision limit or stale revision")
        if not note.strip() or len(note) > 2000:
            raise ValueError("revision note required")
        from app.subagents import SupervisorDispatchRequest
        request = SupervisorDispatchRequest(worker_profile="code_analyst", scope=scope, workspace=proposal.workspace, instruction=f"Revise proposal {proposal.proposal_id}. Objective: {proposal.objective[:1200]}. Prior changes: {[(c.relative_path, c.reason) for c in proposal.changes][:5]}. Supervisor note: {note[:2000]}", parent_session_id=None, mode="one_shot")
        authorization = self.workflows.agents.authorize_dispatch(request)
        result = await self.workflows.agents.dispatch(request, authorization)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO proposal_revision_handoffs(task_id, scope, workflow_id, parent_proposal_id, note, materialized_proposal_id) VALUES (?, ?, ?, ?, ?, NULL)", (result.task_id, scope, proposal.workflow_id, proposal.proposal_id, note[:2000]))
        return {"proposal_id": proposal.proposal_id, "revision_number": proposal.revision_number + 1, "parent_proposal_id": proposal.proposal_id, "task_id": result.task_id, "status": result.status, "note": note[:2000]}

    def revision_candidate(self, workflow_id: str, scope: str) -> dict:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT task_id, parent_proposal_id, note, materialized_proposal_id FROM proposal_revision_handoffs WHERE workflow_id=? AND scope=? ORDER BY rowid DESC LIMIT 1", (workflow_id, scope)).fetchone()
        if row is None:
            raise ValueError("revision candidate unavailable")
        task_id, parent_id, note, materialized = row
        parent = self.get(parent_id, scope)
        record = self.workflows.agents.get(task_id)
        review = self.workflows.agents.get_review(task_id, scope)
        if parent is None or record is None or review is None or review.status != "accepted" or record[1] is None:
            raise ValueError("accepted reviewed analyst result required")
        result = record[1]
        try:
            payload = json.loads(result.summary)
            candidate = StructuredOutputService.validate_json(
                json.dumps(payload.get("proposal", payload), sort_keys=True), RevisionCandidate
            )
        except (StructuredOutputError, ValueError, TypeError, json.JSONDecodeError):
            raise ValueError("invalid revision candidate") from None
        return {"workflow_id": workflow_id, "parent_proposal_id": parent_id, "revision_number": parent.revision_number + 1, "task_id": task_id, "review_status": review.status, "note": note, "materialized_proposal_id": materialized, "summary": result.summary[:2000], "candidate": candidate.model_dump()}

    def materialize_revision_candidate(self, workflow_id: str, scope: str) -> CodingProposal:
        candidate = self.revision_candidate(workflow_id, scope)
        if candidate["materialized_proposal_id"]:
            return self.get(candidate["materialized_proposal_id"], scope)  # type: ignore[return-value]
        parent = self.get(candidate["parent_proposal_id"], scope)
        if parent is None or parent.status != "rejected" or parent.conversion_status == "converted":
            raise ValueError("parent proposal cannot be revised")
        if parent.revision_number >= 5:
            raise ValueError("revision limit or stale revision")
        request = ProposalCreate(**candidate["candidate"])
        with SQLiteDatabase(self.path).transaction(immediate=True) as connection:
            handoff = connection.execute("SELECT materialized_proposal_id FROM proposal_revision_handoffs WHERE task_id=? AND scope=?", (candidate["task_id"], scope)).fetchone()
            if handoff is None:
                raise ValueError("revision candidate unavailable")
            if handoff[0]:
                return self.get(handoff[0], scope)  # type: ignore[return-value]
            parent_row = connection.execute("SELECT payload_json FROM coding_proposals WHERE proposal_id=?", (parent.proposal_id,)).fetchone()
            current_parent = CodingProposal.model_validate_json(parent_row[0]) if parent_row else None
            if current_parent is None or current_parent.status != "rejected" or current_parent.superseded_by_proposal_id:
                raise ValueError("parent proposal cannot be revised")
            revision = self.create_with_connection(connection, workflow_id, scope, request, parent_proposal_id=current_parent.proposal_id, revision_number=current_parent.revision_number + 1, revision_note=candidate["note"])
            self._fail("parent_supersession")
            connection.execute("UPDATE coding_proposals SET payload_json=? WHERE proposal_id=?", (current_parent.model_copy(update={"superseded_by_proposal_id": revision.proposal_id}).model_dump_json(), current_parent.proposal_id))
            self._fail("handoff_linkage")
            connection.execute("UPDATE proposal_revision_handoffs SET materialized_proposal_id=? WHERE task_id=? AND scope=? AND materialized_proposal_id IS NULL", (revision.proposal_id, candidate["task_id"], scope))
            return revision

    def create_revision(self, workflow_id: str, scope: str, parent_proposal_id: str, request: ProposalCreate, note: str) -> CodingProposal:
        parent = self.get(parent_proposal_id, scope)
        if parent is None or parent.workflow_id != workflow_id or parent.status != "rejected" or parent.conversion_status == "converted":
            raise ValueError("parent proposal cannot be revised")
        history = self.history(workflow_id, scope, 5)
        if parent.revision_number != max(item["revision_number"] for item in history) or parent.revision_number >= 5:
            raise ValueError("revision limit or stale revision")
        if not note.strip() or len(note) > 2000:
            raise ValueError("revision note required")
        created = self.create(workflow_id, scope, request)
        revised = created.model_copy(update={"revision_number": parent.revision_number + 1, "parent_proposal_id": parent.proposal_id, "revision_note": note[:2000]})
        self._save(revised)
        self._save(parent.model_copy(update={"superseded_by_proposal_id": revised.proposal_id}))
        return revised

    def accept_specs(self, proposal_id: str, scope: str, workspace: str, reviewer_session_id: str | None = None) -> CodingProposal:
        proposal = self.get(proposal_id, scope)
        if proposal is None or proposal.workspace != workspace or proposal.conversion_status != "converted": raise ValueError("proposal specs unavailable")
        if proposal.spec_review_status != "pending": return proposal
        if reviewer_session_id and reviewer_session_id == proposal.analyst_task_id: raise ValueError("worker cannot review specs")
        status = self.git.status(workspace); dirty = {line[3:].strip().split(" -> ")[-1] for line in status.splitlines() if line and not line.startswith("##")}
        for change in proposal.changes:
            path = self.workspaces.resolve_path(workspace, change.relative_path)
            if change.relative_path in dirty or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != proposal.target_hashes.get(change.relative_path) or change.expected_text not in path.read_text(encoding="utf-8", errors="replace"):
                raise ValueError("proposal specs are stale")
        now = datetime.now(UTC)
        updated = proposal.model_copy(update={"spec_review_status": "accepted", "spec_reviewed_at": now, "updated_at": now})
        with SQLiteDatabase(self.path).transaction(immediate=True) as connection:
            self.workflows.specs.mark_ready_many_with_connection(connection, [*proposal.patch_spec_ids, *proposal.check_spec_ids], scope, failure_injector=self.failure_injector, require_actionable=False)
            self._fail("review_metadata")
            CodingProposalService.save_with_connection(connection, updated)
        return updated

    def reject_specs(self, proposal_id: str, scope: str, reviewer_session_id: str | None = None) -> CodingProposal:
        proposal = self.get(proposal_id, scope)
        if proposal is None or proposal.conversion_status != "converted": raise ValueError("proposal specs unavailable")
        if reviewer_session_id and reviewer_session_id == proposal.analyst_task_id: raise ValueError("worker cannot review specs")
        if proposal.spec_review_status != "pending": return proposal
        updated = proposal.model_copy(update={"spec_review_status": "rejected", "spec_reviewed_at": datetime.now(UTC), "updated_at": datetime.now(UTC)})
        self._save(updated); return updated

    def get(self, proposal_id: str, scope: str) -> CodingProposal | None:
        with sqlite3.connect(self.path) as db: row = db.execute("SELECT payload_json FROM coding_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
        if not row: return None
        item = CodingProposal.model_validate_json(row[0]); return item if item.scope == scope else None

    def review(self, proposal_id: str, scope: str, status: str, reviewer_session_id: str | None = None, note: str | None = None) -> CodingProposal:
        item = self.get(proposal_id, scope)
        if item is None or status not in {"accepted", "rejected"}: raise ValueError("proposal unavailable")
        if reviewer_session_id and reviewer_session_id == item.analyst_task_id: raise ValueError("worker cannot review proposal")
        if item.status not in {"ready_for_review", "accepted"}: return item
        updated = item.model_copy(update={"status": status, "revision_note": (note[:2000] if note else item.revision_note), "updated_at": datetime.now(UTC)})
        self._save(updated); return updated

    def _save(self, item: CodingProposal) -> None:
        with sqlite3.connect(self.path) as db: db.execute("INSERT OR REPLACE INTO coding_proposals VALUES (?, ?)", (item.proposal_id, item.model_dump_json()))

    @staticmethod
    def save_with_connection(connection: sqlite3.Connection, item: CodingProposal) -> None:
        connection.execute("INSERT OR REPLACE INTO coding_proposals VALUES (?, ?)", (item.proposal_id, item.model_dump_json()))

    def _fail(self, label: str) -> None:
        if self.failure_injector is not None:
            self.failure_injector(label)
