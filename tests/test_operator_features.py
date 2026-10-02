import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as Item

import pytest
from fastapi.testclient import TestClient

from app import main
from app.mcp.service import MCPAdapter, MCPServerConfig
from app.operator.integrations import IntegrationControls
from app.operator.tasks import project_progress, shortcut_request, shortcut_warnings
from app.operator.worktrees import ManagedWorktrees
from app.policy.workspaces import WorkspaceRegistry
from app.skills.service import SkillService
from app.tools.code_search import WorkspaceCodeSearch
from app.tools.registry import ToolRegistry


def test_builtin_shortcut_uses_skills_and_preserves_quoted_paths(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    target = "space name.py"
    (root / target).write_text('def greeting(): return "José"', encoding="utf-8")
    search = WorkspaceCodeSearch(WorkspaceRegistry({"project": root}))
    skills = SkillService(tmp_path / "user", Path(main.__file__).parent / "skills/builtin")
    request = shortcut_request(
        "project", target, "review-code", skills, search, "Keep the contract"
    )
    name, args = SkillService.invocation(request.message)
    assert name == "review-code" and args == {"target": target, "note": "Keep the contract"}
    assert set(skills.get(name).allowed_tools) == {
        "filesystem.read",
        "filesystem.grep",
        "filesystem.glob",
        "workspace.symbols",
    }
    assert "Read only" in skills.render(skills.get(name), args)
    with pytest.raises(PermissionError):
        shortcut_request("project", "../outside.py", name, skills, search)
    with pytest.raises(ValueError):
        shortcut_request("project", target, name, skills, search, "invalid\ncontext")
    with pytest.raises(ValueError):
        SkillService.invocation('/skill review-code {"target": 42}')


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, timeout=20
    ).stdout.strip()


def test_isolation_preserves_dirty_original_and_restores_registered_copy(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "example.py").write_text("saved = True\n")
    git(root, "add", "example.py")
    git(root, "commit", "-m", "baseline")
    (root / "example.py").write_text("unsaved = True\n")
    registry = WorkspaceRegistry({"project": root})
    manager = ManagedWorktrees(registry, tmp_path / "records.sqlite", tmp_path / "copies")
    preview = manager.preview("project")
    assert preview["has_unsaved_changes"] and not preview["copies_unsaved_changes"]
    with pytest.raises(ValueError):
        manager.create("project", "0" * 40)
    record = manager.create("project", preview["base_commit"])
    assert record["status"] == "ready" and record["branch"].startswith("codex/")
    assert registry.resolve_path(record["workspace"], "example.py").read_text() == "saved = True\n"
    assert (root / "example.py").read_text() == "unsaved = True\n"
    assert git(root, "branch", "--show-current") == preview["branch"]
    restored = WorkspaceRegistry({"project": root})
    ManagedWorktrees(restored, tmp_path / "records.sqlite", tmp_path / "copies")
    assert restored.get_root(record["workspace"]) == Path(record["path"])
    with pytest.raises(ValueError):
        manager.preview(record["workspace"])
    with pytest.raises(PermissionError):
        manager.preview("unknown")


def test_isolation_rejects_registered_subfolder(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init")
    sub = root / "sub"
    sub.mkdir()
    manager = ManagedWorktrees(
        WorkspaceRegistry({"sub": sub}), tmp_path / "records.sqlite", tmp_path / "copies"
    )
    with pytest.raises(ValueError, match="whole Git project"):
        manager.preview("sub")


def test_integration_controls_real_stdio_disconnect_reconnect_and_risk(tmp_path, monkeypatch):
    fixture = Path(__file__).parent / "fixtures/mcp_stdio_server.py"
    adapter = MCPAdapter(
        [
            MCPServerConfig(
                name=name,
                transport="stdio",
                command=sys.executable,
                args=[str(fixture)],
                read_only=read_only,
            )
            for name, read_only in [("local", True), ("unsafe", False)]
        ]
    )
    tools = ToolRegistry()
    controls = IntegrationControls(adapter, tools)
    monkeypatch.setattr(main, "integration_controls", controls)
    http = TestClient(main.app)
    try:
        response = http.post("/operator/integrations/local/action", json={"action": "connect"})
        assert response.status_code == 200 and response.json()["connected"]
        assert http.get("/operator/integrations").json()[0]["tools"][0]["enabled"]
        assert "mcp.local.echo" in tools.names()
        assert adapter.call("mcp.local.echo", {"text": "live"}).is_error is False
        controls.action("local", "disconnect")
        assert "mcp.local.echo" not in tools.names() and adapter.call("mcp.local.echo", {}).is_error
        assert controls.action("local", "connect")["connected"]
        assert controls.action("local", "connect")["tool_count"] == 1
        unsafe = controls.action("unsafe", "connect")
        assert unsafe["tools"][0]["enabled"] is False and "mcp.unsafe.echo" not in tools.names()
        with pytest.raises(ValueError):
            controls.action("unconfigured", "connect")
    finally:
        controls.action("local", "disconnect")
        controls.action("unsafe", "disconnect")


def test_worker_progress_uses_saved_results_and_excludes_other_context(tmp_path):
    registry = WorkspaceRegistry({"project": tmp_path})
    workflow = Item(workflow_id="work")
    job = Item(
        workflow_id="work",
        objective="Review",
        status="awaiting_review",
        next_action=Item(action="review_analysis"),
        blocked_reason=None,
        analyst_task_id="analyst",
        verifier_task_id="other",
        proposal_status=None,
        patch_specs=[],
        check_specs=[],
    )
    workers = Item(
        get=lambda task_id: (
            Item(
                scope="scope",
                workspace="project" if task_id == "analyst" else "other",
                status="created",
            ),
            Item(status="completed", safe_reason=None),
        )
    )
    empty = Item(list=lambda *args: [])
    result = project_progress(
        "scope",
        "project",
        Item(list=lambda *args: [workflow]),
        Item(get=lambda *args: job),
        workers,
        empty,
        registry,
    )
    assert result["workflows"][0]["workers"] == [
        {"role": "Analysis", "task_id": "analyst", "status": "completed", "reason": None}
    ]
    assert result["workflows"][0]["next_action"] == "review_analysis"


def test_feature_routes_reject_unknown_context_and_expose_safe_ui():
    client = TestClient(main.app)
    assert len(client.get("/operator/shortcuts").json()) == 4
    assert (
        client.post(
            "/operator/shortcuts/review-code", json={"workspace": "unknown", "target": "example.py"}
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/operator/progress", params={"scope": "test", "workspace": "unknown"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/operator/integrations/unconfigured/action", json={"action": "connect"}
        ).status_code
        == 400
    )
    asset = client.get("/operator-features.js").text
    assert "innerHTML" not in asset and "Approve memory" in asset and "Preview isolation" in asset
    assert "/operator-features.js" in client.get("/operator").text


def test_failed_integration_never_exposes_partial_capabilities():
    class BrokenClient:
        closed = False

        def connect(self):
            pass

        def close(self):
            self.closed = True

        def list_tools(self):
            return [{"name": "partial"}]

        def list_resources(self):
            raise ValueError("private configuration detail")

    adapter = MCPAdapter([MCPServerConfig(name="broken")])
    client = BrokenClient()
    adapter.connect("broken", client)
    registry = ToolRegistry()
    adapter.register_tools(registry)
    assert client.closed and not adapter.tools() and not registry.names()
    assert "private configuration" not in str(IntegrationControls(adapter, registry).catalog())


def test_read_only_reviews_flag_unexecuted_test_claims():
    assert shortcut_warnings("All tests pass against the implementation.")
    assert shortcut_warnings("I ran pytest and found no failures.")
    assert not shortcut_warnings("No tests were run. I cannot say tests passed.")
