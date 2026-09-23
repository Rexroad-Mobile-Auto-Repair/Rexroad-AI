from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.memory.proposals import ProposalService
from app.memory.service import MemoryService
from app.memory.store import MemoryStore


def test_proposal_review_api_is_scoped_and_explicit(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "review.sqlite3"
    memories = MemoryService(MemoryStore(path))
    proposals = ProposalService(path, memories)
    monkeypatch.setattr(main, "memory_service", memories)
    monkeypatch.setattr(main, "proposal_service", proposals)
    client = TestClient(main.app)

    created = client.post("/memory-proposals", json={
        "scope": "project-a", "category": "decision", "proposed_content": "Use SQLite",
        "session_reference": "session-1", "metadata": {"source": "review"},
    }).json()
    proposal_id = created["id"]
    assert client.get("/memory-proposals", params={"scope": "project-a"}).json()[0]["id"] == proposal_id
    assert client.get("/memory-proposals", params={"scope": "project-b"}).json() == []
    assert client.get(f"/memory-proposals/{proposal_id}", params={"scope": "project-b"}).status_code == 404
    assert memories.list("project-a") == []

    approved = client.post(f"/memory-proposals/{proposal_id}/approve", params={"scope": "project-a"})
    assert approved.status_code == 200
    assert approved.json()["session_reference"] == "session-1"
    assert len(memories.list("project-a")) == 1
    assert client.post(f"/memory-proposals/{proposal_id}/approve", params={"scope": "project-a"}).json()["approved_memory_id"] == approved.json()["approved_memory_id"]


def test_reject_review_api_creates_no_memory_and_unknown_is_safe(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "review.sqlite3"
    memories = MemoryService(MemoryStore(path))
    proposals = ProposalService(path, memories)
    monkeypatch.setattr(main, "memory_service", memories)
    monkeypatch.setattr(main, "proposal_service", proposals)
    client = TestClient(main.app)
    created = client.post("/memory-proposals", json={
        "scope": "project-a", "category": "note", "proposed_content": "Keep this",
    }).json()
    proposal_id = created["id"]
    assert client.post(f"/memory-proposals/{proposal_id}/reject", params={"scope": "project-a"}).status_code == 200
    assert client.post(f"/memory-proposals/{proposal_id}/approve", params={"scope": "project-a"}).status_code == 409
    assert memories.list("project-a") == []
    assert client.get("/memory-proposals/missing", params={"scope": "project-a"}).status_code == 404
