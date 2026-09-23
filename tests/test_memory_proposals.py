from pathlib import Path

import pytest

from app.memory.proposals import MemoryProposalCreate, ProposalService
from app.memory.service import MemoryService
from app.memory.store import MemoryStore


def services(tmp_path: Path) -> tuple[MemoryService, ProposalService]:
    path = tmp_path / "journal.sqlite3"
    memories = MemoryService(MemoryStore(path))
    return memories, ProposalService(path, memories)


def test_proposal_is_not_memory_until_approved_and_is_idempotent(tmp_path):
    memories, proposals = services(tmp_path)
    proposal = proposals.create(MemoryProposalCreate(scope="a", category="decision", proposed_content="Use A", session_reference="session-1", provenance="research_outcome", metadata={"source": "review"}))
    assert memories.list("a") == []
    approved = proposals.approve(proposal.id)
    again = proposals.approve(proposal.id)
    assert approved.status == "approved"
    assert approved.approved_memory_id == again.approved_memory_id
    assert len(memories.list("a")) == 1
    memory = memories.get(approved.approved_memory_id)
    assert memory.scope == "a"
    assert memory.category == "decision"
    assert memory.source_reference == "session-1"
    assert memory.provenance == "research_outcome"
    assert memory.metadata == {"source": "review"}


def test_rejection_and_invalid_lifecycle(tmp_path):
    memories, proposals = services(tmp_path)
    rejected = proposals.create(MemoryProposalCreate(scope="a", category="task", proposed_content="Do A"))
    assert proposals.reject(rejected.id).status == "rejected"
    assert proposals.approve(rejected.id) is None
    assert memories.list("a") == []
    assert proposals.get("missing") is None


def test_scope_ordering_and_restart_persistence(tmp_path):
    memories, proposals = services(tmp_path)
    first = proposals.create(MemoryProposalCreate(scope="a", category="note", proposed_content="one"))
    proposals.create(MemoryProposalCreate(scope="b", category="note", proposed_content="two"))
    assert [item.id for item in proposals.list("a")] == [first.id]
    approved = proposals.approve(first.id)
    restarted_memories = MemoryService(MemoryStore(tmp_path / "journal.sqlite3"))
    restarted = ProposalService(tmp_path / "journal.sqlite3", restarted_memories)
    persisted = restarted.get(first.id)
    assert persisted.proposed_content == "one"
    assert persisted.status == "approved"
    assert persisted.approved_memory_id == approved.approved_memory_id
    assert restarted_memories.get(persisted.approved_memory_id).content == "one"
    assert len(memories.list("a")) == 1


def test_approval_failure_rolls_back_memory_and_proposal(tmp_path, monkeypatch):
    memories, proposals = services(tmp_path)
    proposal = proposals.create(MemoryProposalCreate(scope="a", category="decision", proposed_content="Use A"))
    original = proposals._update_proposal
    def fail(*args):
        original(*args)
        raise RuntimeError("injected failure")
    monkeypatch.setattr(proposals, "_update_proposal", fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        proposals.approve(proposal.id)
    assert proposals.get(proposal.id).status == "pending"
    assert memories.list("a") == []
