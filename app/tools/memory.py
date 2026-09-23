from __future__ import annotations

from typing import Any

from app.memory.models import MemoryCategory, MemoryProvenance
from app.memory.proposals import MemoryProposalCreate, ProposalService
from app.memory.service import MemoryService


class MemoryTools:
    def __init__(self, memories: MemoryService, proposals: ProposalService) -> None:
        self._memories = memories
        self._proposals = proposals

    def search(
        self,
        scope: str,
        query: str,
        limit: int = 20,
        category: MemoryCategory | None = None,
        status: str = "active",
    ) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("memory query must be nonblank")
        return [record.model_dump(mode="json") for record in self._memories.search(
            scope, query, limit, category=category, status=status
        )]

    def propose(
        self,
        scope: str,
        category: MemoryCategory,
        content: str,
        provenance: MemoryProvenance = "agent_recorded",
        session_reference: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        proposal = self._proposals.create(MemoryProposalCreate(
            scope=scope, category=category, proposed_content=content,
            provenance=provenance, session_reference=session_reference,
            metadata=metadata or {},
        ))
        return proposal.model_dump(mode="json")

    def proposal_status(self, scope: str, proposal_id: str) -> dict[str, Any]:
        proposal = self._proposals.get(proposal_id)
        if proposal is None or proposal.scope != scope:
            raise ValueError("memory proposal not found")
        return proposal.model_dump(mode="json")
