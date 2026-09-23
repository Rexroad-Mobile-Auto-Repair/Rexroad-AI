from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from app.memory.models import MemoryCategory, MemoryProvenance, MemoryRecord
from app.memory.service import MemoryService

ProposalStatus = Literal["pending", "approved", "rejected"]


class MemoryProposal(BaseModel):
    id: str
    scope: str
    category: MemoryCategory
    proposed_content: str
    provenance: MemoryProvenance
    session_reference: str | None = None
    status: ProposalStatus
    approved_memory_id: str | None = None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("scope", "proposed_content")
    @classmethod
    def _safe_text(cls, value: str) -> str:
        if not value.strip() or "\r" in value or "\n" in value:
            raise ValueError("proposal text must be nonblank and single-line")
        return value


class MemoryProposalCreate(BaseModel):
    scope: str
    category: MemoryCategory
    proposed_content: str
    provenance: MemoryProvenance = "user_explicit"
    session_reference: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProposalService:
    def __init__(self, database_path: str | Path, memories: MemoryService) -> None:
        self._database_path = Path(database_path)
        self._memories = memories
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS memory_proposals (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, category TEXT NOT NULL,
                proposed_content TEXT NOT NULL, provenance TEXT NOT NULL,
                session_reference TEXT, status TEXT NOT NULL, approved_memory_id TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL
            )""")

    def create(self, request: MemoryProposalCreate) -> MemoryProposal:
        now = datetime.now(UTC)
        proposal = MemoryProposal(id=str(uuid4()), scope=request.scope, category=request.category,
            proposed_content=request.proposed_content, provenance=request.provenance,
            session_reference=request.session_reference, status="pending", created_at=now, updated_at=now,
            metadata=request.metadata)
        self._put(proposal)
        return proposal

    def get(self, proposal_id: str, scope: str | None = None) -> MemoryProposal | None:
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            if scope is None:
                row = connection.execute("SELECT * FROM memory_proposals WHERE id = ?", (proposal_id,)).fetchone()
            else:
                row = connection.execute("SELECT * FROM memory_proposals WHERE id = ? AND scope = ?", (proposal_id, scope)).fetchone()
        return self._row(row) if row else None

    def list(self, scope: str, status: ProposalStatus = "pending", limit: int = 50) -> list[MemoryProposal]:
        if not scope.strip() or limit < 1 or limit > 100:
            raise ValueError("invalid proposal scope or limit")
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM memory_proposals WHERE scope = ? AND status = ? ORDER BY created_at, id LIMIT ?", (scope, status, limit)).fetchall()
        return [self._row(row) for row in rows]

    def approve(self, proposal_id: str, scope: str | None = None) -> MemoryProposal | None:
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            if scope is None:
                row = connection.execute("SELECT * FROM memory_proposals WHERE id = ?", (proposal_id,)).fetchone()
            else:
                row = connection.execute("SELECT * FROM memory_proposals WHERE id = ? AND scope = ?", (proposal_id, scope)).fetchone()
            if row is None or row["status"] == "rejected":
                return None
            proposal = self._row(row)
            if proposal.status == "approved":
                return proposal
            now = datetime.now(UTC)
            memory = MemoryRecord(id=str(uuid4()), scope=proposal.scope, category=proposal.category,
                content=proposal.proposed_content, status="active", provenance=proposal.provenance,
                source_reference=proposal.session_reference, created_at=now, updated_at=now,
                metadata=proposal.metadata)
            self._insert_memory(connection, memory)
            approved = proposal.model_copy(update={"status": "approved", "approved_memory_id": memory.id, "updated_at": now})
            self._update_proposal(connection, approved)
            return approved

    def reject(self, proposal_id: str, scope: str | None = None) -> MemoryProposal | None:
        proposal = self.get(proposal_id, scope)
        if proposal is None or proposal.status != "pending":
            return None
        rejected = proposal.model_copy(update={"status": "rejected", "updated_at": datetime.now(UTC)})
        self._put(rejected)
        return rejected

    def _put(self, proposal: MemoryProposal) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("""INSERT OR REPLACE INTO memory_proposals
                (id, scope, category, proposed_content, provenance, session_reference, status,
                 approved_memory_id, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (proposal.id, proposal.scope, proposal.category,
                proposal.proposed_content, proposal.provenance, proposal.session_reference, proposal.status,
                proposal.approved_memory_id, proposal.created_at.isoformat(), proposal.updated_at.isoformat(),
                json.dumps(proposal.metadata, ensure_ascii=False, sort_keys=True)))

    @staticmethod
    def _insert_memory(connection: sqlite3.Connection, memory: MemoryRecord) -> None:
        connection.execute("""INSERT INTO memory_records
            (id, scope, category, content, status, provenance, source_reference, supersedes, created_at, updated_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (memory.id, memory.scope, memory.category, memory.content,
            memory.status, memory.provenance, memory.source_reference, memory.supersedes, memory.created_at.isoformat(),
            memory.updated_at.isoformat(), json.dumps(memory.metadata, ensure_ascii=False, sort_keys=True)))

    @staticmethod
    def _update_proposal(connection: sqlite3.Connection, proposal: MemoryProposal) -> None:
        connection.execute("UPDATE memory_proposals SET status = ?, approved_memory_id = ?, updated_at = ? WHERE id = ?",
                           (proposal.status, proposal.approved_memory_id, proposal.updated_at.isoformat(), proposal.id))

    @staticmethod
    def _row(row: sqlite3.Row) -> MemoryProposal:
        return MemoryProposal(id=row["id"], scope=row["scope"], category=row["category"], proposed_content=row["proposed_content"],
            provenance=row["provenance"], session_reference=row["session_reference"], status=row["status"],
            approved_memory_id=row["approved_memory_id"], created_at=row["created_at"], updated_at=row["updated_at"],
            metadata=json.loads(row["metadata_json"]))
