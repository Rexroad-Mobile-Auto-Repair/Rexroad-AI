from app.project_briefing import ProjectBriefingService


def test_briefing_uses_project_state_sources():
    class State:
        def get(self, workspace, scope):
            from types import SimpleNamespace
            return SimpleNamespace(workspace=workspace, scope=scope, branch="main", head="abc", clean=False, changed_files=["a.txt"], active_plans=[{"goal": "ship",}], next_steps=[{"step": {"title": "test"}}], unresolved_tasks=[{"content": "follow up"}], recent_traces=[SimpleNamespace(tool="x", status="failed")])
    result = ProjectBriefingService(State()).build("repo", "s")
    assert result.status == "fallback"
    assert "ship" in result.summary and "failed" in result.summary
