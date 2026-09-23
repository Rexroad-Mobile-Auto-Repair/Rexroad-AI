from fastapi.testclient import TestClient

from app import main
from app.tools.registry import ToolRegistry


def test_approval_review_api_never_exposes_capability(monkeypatch):
    client = TestClient(main.app)
    registry = main.tool_registry
    auth = registry.authorize("filesystem.read", "review", "session-1")
    tool = registry.get("filesystem.read")
    original = tool.high_impact
    object.__setattr__(tool, "high_impact", True)
    try:
        request = registry.request_approval(auth, {"path": "safe"}, "Read approved file")
        response = client.get(f"/tool-approvals/{request.id}", params={"scope": "review"})
        assert response.status_code == 200
        assert "token" not in response.text
        assert client.get(f"/tool-approvals/{request.id}", params={"scope": "other"}).status_code == 404
        assert client.post(f"/tool-approvals/{request.id}/approve", params={"scope": "review"}).status_code == 200
        assert client.post(f"/tool-approvals/{request.id}/reject", params={"scope": "review"}).json()["status"] == "approved"
    finally:
        object.__setattr__(tool, "high_impact", original)


def test_approval_request_metadata_survives_restart_without_capability(tmp_path):
    path = tmp_path / "journal.sqlite3"
    first = ToolRegistry(path)
    first.register(main.tool_registry.get("filesystem.read"))
    auth = first.authorize("filesystem.read", "scope", "session")
    tool = first.get("filesystem.read")
    object.__setattr__(tool, "high_impact", True)
    request = first.request_approval(auth, {"path": "safe"}, "safe action")
    first.review_approval(request.id, "scope", True)
    restarted = ToolRegistry(path)
    row = restarted._load_request(request.id, "scope")
    assert row.status == "approved"
    assert not hasattr(row, "token")
