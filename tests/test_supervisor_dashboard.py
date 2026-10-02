from datetime import UTC, datetime
from types import SimpleNamespace

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
    workflow = type("Workflow", (), {"workflow_id": "wf", "workspace": "a", "updated_at": NOW, "status": "implementing", "job": job, "guidance": guidance})()
    result = service([workflow]).get("scope", "a")
    assert result.attention[0].category == "action_ready"
    assert result.attention[0].id == "coding:wf:execute_patches"
    assert result.attention[0].high_impact is True
    assert result.summary.ready_actions == 1


def test_unknown_workspace_is_rejected_without_data():
    with pytest.raises(KeyError):
        service().get("scope", "unknown")


@pytest.mark.parametrize("verified", [True, False])
def test_only_proven_recovered_check_plans_move_to_history(verified):
    job = CodingJob.model_construct(workflow_id="wf", job_id="wf", scope="scope", workspace="a", objective="edit", status="completed" if verified else "failed", next_action=CodingJobAction(action="none", allowed=False, reason="terminal"), observed_at=NOW)
    workflow = SimpleNamespace(workflow_id="wf", status=job.status, outcome="verified" if verified else "check_failed", plan_id="replacement", check_trace_ids=["passed"], execution_attempts=[SimpleNamespace(plan_id="old", kind="check", status="failed")], job=job, guidance=None)
    dashboard = service([workflow])
    dashboard.plans = SimpleNamespace(list=lambda *args: [SimpleNamespace(id=pid, status="failed", workspace="a", steps=[], updated_at=NOW) for pid in ["old", "unrelated"]])
    result = dashboard.get("scope", "a")
    plan_attention = [x.source_id for x in result.attention if x.source_type == "plan"]
    assert "unrelated" in plan_attention
    assert ("old" in plan_attention) is not verified
    assert [x.source_id for x in result.history] == (["old"] if verified else [])
    assert all(p.status == "failed" for p in result.plans)
    assert workflow.status == job.status


@pytest.mark.parametrize("attempts", [[], [SimpleNamespace(kind="patch", status="failed")]])
def test_stopped_read_only_attempt_is_history_but_patch_failure_remains_visible(attempts):
    job = CodingJob.model_construct(workflow_id="wf", job_id="wf", scope="scope", workspace="a", objective="edit", status="failed", next_action=CodingJobAction(action="none", allowed=False, reason="terminal"), observed_at=NOW)
    workflow = SimpleNamespace(workflow_id="wf", status="failed", execution_attempts=attempts, job=job, guidance=None)
    result = service([workflow]).get("scope", "a")
    assert bool(result.attention) == bool(attempts)
    assert bool(result.history) is not bool(attempts)
    assert result.coding[0].status == "failed"


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


def test_operator_page_is_human_surface_without_capabilities_or_patch_payloads():
    client = TestClient(main.app)
    response = client.get("/operator")
    assert response.status_code == 200
    assert "Rexroad AI" in response.text
    assert "Create coding workflow" in response.text
    assert "ToolAuthorization" not in response.text
    assert "ToolApproval" not in response.text
    assert "expected_text" not in response.text
    assert "replacement" not in response.text
    assert '/operator-controls.js' in response.text
    assert "api('/supervisor/dashboard?" in client.get('/operator-controls.js').text


def test_operator_page_has_no_load_time_mutation_endpoint():
    client = TestClient(main.app)
    page = client.get("/operator-controls.js").text
    assert "method:'POST'" in page
    assert "$('create').onclick" in page
    assert "runAction(g.next_action," in page
