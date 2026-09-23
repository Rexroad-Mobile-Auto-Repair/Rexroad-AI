from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app import main
from app.coding_guidance import CodingJobGuidance
from app.coding_jobs import CodingJob, CodingJobAction
from app.project_state import ProjectState
from app.supervisor_dashboard import SupervisorDashboardService

NOW = datetime(2025, 1, 1, tzinfo=UTC)


class Workspaces:
    def get_root(self, workspace: str) -> str:
        if workspace not in {"a", "b"}:
            raise KeyError(workspace)
        return workspace


class State:
    def get(self, workspace: str, scope: str) -> ProjectState:
        return ProjectState(workspace=workspace, available=True, scope=scope, branch="main", head="abc", clean=True, changed_files=[], active_plans=[], next_steps=[], unresolved_tasks=[], recent_traces=[], observed_at=NOW)


class Empty:
    def list(self, *args, **kwargs):
        return []


class Tools:
    def list_approval_requests(self, scope: str, limit: int, status: str):
        return []


def service(coding=None):
    class Workflows:
        def list(self, scope, workspace, limit):
            return coding or []

    class Jobs:
        def get(self, workflow_id, scope):
            return coding[0].job if coding else None

    class Guidance:
        def get(self, workflow_id, scope):
            return coding[0].guidance if coding else None

    return SupervisorDashboardService(Workspaces(), State(), Workflows(), Jobs(), Guidance(), Empty(), Empty(), Empty(), Tools())


def test_empty_dashboard_is_deterministic_and_read_only():
    dashboard = service().get("scope", "a")
    assert dashboard.attention == []
    assert dashboard.summary.model_dump() == {
        "active_coding_jobs": 0,
        "active_research_workflows": 0,
        "active_plans": 0,
        "blocked_items": 0,
        "pending_approvals": 0,
        "pending_reviews": 0,
        "ready_actions": 0,
    }
    assert dashboard.project["workspace"] == "a"


def test_coding_attention_uses_guidance_and_stable_id():
    job = CodingJob.model_construct(workflow_id="wf", job_id="wf", scope="scope", workspace="a", objective="edit", status="ready_to_execute_patches", next_action=CodingJobAction(action="execute_patches", allowed=True, reason="approved"), observed_at=NOW)
    guidance = CodingJobGuidance.model_construct(workflow_id="wf", scope="scope", workspace="a", job_status=job.status, next_action="execute_patches", action_available=True, headline="Next explicit action: execute_patches.", explanation="approved", why_now="now", affected_resources=["app/x.py"], confirmation_required=True, high_impact=True, observed_at=NOW)
    workflow = type("Workflow", (), {"workflow_id": "wf", "workspace": "a", "updated_at": NOW, "job": job, "guidance": guidance})()
    result = service([workflow]).get("scope", "a")
    assert result.attention[0].category == "action_ready"
    assert result.attention[0].id == "coding:wf:execute_patches"
    assert result.attention[0].high_impact is True
    assert result.summary.ready_actions == 1


def test_unknown_workspace_is_rejected_without_data():
    with pytest.raises(KeyError):
        service().get("scope", "unknown")


def test_dashboard_api_requires_explicit_scope_and_workspace(monkeypatch):
    dashboard = service().get("scope", "a")
    class FakeDashboard:
        def get(self, scope, workspace, **kwargs):
            if scope != "scope" or workspace != "a":
                raise KeyError("not found")
            return dashboard

    monkeypatch.setattr(main, "supervisor_dashboard_service", FakeDashboard())
    client = TestClient(main.app)
    assert client.get("/supervisor/dashboard", params={"scope": "scope", "workspace": "a"}).status_code == 200
    assert client.get("/supervisor/dashboard", params={"scope": "wrong", "workspace": "a"}).status_code == 404
