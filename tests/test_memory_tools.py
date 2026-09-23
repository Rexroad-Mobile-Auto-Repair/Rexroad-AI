from pathlib import Path

import pytest

from app.memory.models import MemoryCreate
from app.memory.proposals import MemoryProposalCreate, ProposalService
from app.memory.service import MemoryService
from app.memory.store import MemoryStore
from app.policy.workspaces import WorkspaceRegistry
from app.tools.factory import build_tool_registry
from app.tools.filesystem import ReadOnlyFilesystem
from app.tools.git import ReadOnlyGit
from app.tools.memory import MemoryTools


def services(tmp_path: Path) -> tuple[MemoryService, ProposalService]:
    path = tmp_path / "memory.sqlite3"
    memories = MemoryService(MemoryStore(path))
    return memories, ProposalService(path, memories)


def test_memory_search_is_scoped_bounded_and_filterable(tmp_path: Path) -> None:
    memories, proposals = services(tmp_path)
    memories.create(MemoryCreate(scope="a", category="decision", content="use sqlite", metadata={"n": 1}))
    memories.create(MemoryCreate(scope="b", category="decision", content="use sqlite"))
    memories.create(MemoryCreate(scope="a", category="note", content="use sqlite notes"))
    tools = MemoryTools(memories, proposals)

    results = tools.search("a", "sqlite", category="decision", limit=1)
    assert len(results) == 1
    assert results[0]["scope"] == "a"
    assert results[0]["category"] == "decision"
    with pytest.raises(ValueError):
        tools.search("", "sqlite")


def test_memory_propose_never_creates_memory_and_preserves_provenance(tmp_path: Path) -> None:
    memories, proposals = services(tmp_path)
    tools = MemoryTools(memories, proposals)
    result = tools.propose("a", "decision", "use sqlite", "agent_recorded", "session-1", {"source": "tool"})
    assert memories.list("a") == []
    saved = proposals.get(result["id"])
    assert saved.status == "pending"
    assert saved.session_reference == "session-1"
    assert saved.metadata == {"source": "tool"}
    assert "memory.approve" not in {"memory.search", "memory.propose", "memory.proposal_status"}


def test_proposal_status_cannot_cross_scope(tmp_path: Path) -> None:
    memories, proposals = services(tmp_path)
    proposal = proposals.create(MemoryProposalCreate(scope="a", category="note", proposed_content="keep"))
    tools = MemoryTools(memories, proposals)
    assert tools.proposal_status("a", proposal.id)["status"] == "pending"
    with pytest.raises(ValueError):
        tools.proposal_status("b", proposal.id)


def test_registry_exposes_only_search_and_propose_memory_tools(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    memories, proposals = services(tmp_path)
    registry = build_tool_registry(
        ReadOnlyFilesystem(WorkspaceRegistry({"repo": root})),
        ReadOnlyGit(WorkspaceRegistry({"repo": root})),
        memories=memories,
        proposals=proposals,
    )
    assert {name for name in registry.names() if name.startswith("memory.")} == {
        "memory.search", "memory.propose", "memory.proposal_status",
    }
    assert registry.get("memory.propose").permission == "propose"
