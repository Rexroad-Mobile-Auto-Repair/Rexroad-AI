from fastapi.testclient import TestClient

from app import main


class _ProposalAPI:
    def revision_candidate(self, workflow_id: str, scope: str) -> dict:
        if scope != "scope":
            raise ValueError("missing")
        return {"workflow_id": workflow_id, "parent_proposal_id": "parent", "revision_number": 2, "task_id": "task", "review_status": "accepted", "note": "n", "materialized_proposal_id": None, "summary": "safe", "candidate": {"changes": [], "checks": [], "summary": "safe"}}

    def materialize_revision_candidate(self, workflow_id: str, scope: str):
        return type("Proposal", (), {"model_dump": lambda self: {"proposal_id": "child", "workflow_id": workflow_id, "scope": scope}})()


def test_revision_candidate_api_is_read_only_and_scoped(monkeypatch):
    monkeypatch.setattr(main, "coding_proposal_service", _ProposalAPI())
    client = TestClient(main.app)
    response = client.get("/supervisor-coding-workflows/wf/proposal/revision-candidate", params={"scope": "scope"})
    assert response.status_code == 200
    assert response.json()["review_status"] == "accepted"
    assert client.get("/supervisor-coding-workflows/wf/proposal/revision-candidate", params={"scope": "other"}).status_code == 409


def test_revision_materialization_api_returns_safe_error(monkeypatch):
    class Failing(_ProposalAPI):
        def materialize_revision_candidate(self, workflow_id: str, scope: str):
            raise ValueError("cannot")
    monkeypatch.setattr(main, "coding_proposal_service", Failing())
    client = TestClient(main.app)
    response = client.post("/supervisor-coding-workflows/wf/proposal/revision-candidate/materialize", params={"scope": "scope"})
    assert response.status_code == 409
    assert "sqlite" not in response.text and "traceback" not in response.text
