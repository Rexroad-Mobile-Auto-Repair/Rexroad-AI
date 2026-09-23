from pathlib import Path

from app.journal.store import ActionJournal
from app.memory.models import MemoryCreate
from app.memory.service import MemoryService
from app.memory.store import MemoryStore
from app.plans.models import PlanCreate, PlanStepCreate
from app.plans.service import PlanService
from app.plans.traces import ExecutionTraceService
from app.policy.workspaces import WorkspaceRegistry
from app.project_state import ProjectStateService
from app.tools.git import ReadOnlyGit


def test_project_state_aggregates_authoritative_sources(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "note.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init"], cwd=repo, check=True)
    (repo / "note.txt").write_text("changed")
    (repo / "dirty.txt").write_text("dirty")
    journal = ActionJournal(tmp_path / "state.sqlite3")
    plans = PlanService(tmp_path / "state.sqlite3")
    memory = MemoryService(MemoryStore(tmp_path / "state.sqlite3"))
    plan = plans.create(PlanCreate(scope="s", workspace="repo", goal="x", steps=[PlanStepCreate(title="one")]))
    memory.create(MemoryCreate(scope="s", category="task", content="follow up", provenance="manual_system"))
    state = ProjectStateService(WorkspaceRegistry({"repo": repo}), ReadOnlyGit(WorkspaceRegistry({"repo": repo})), memory, plans, ExecutionTraceService(journal)).get("repo", "s")
    assert state.branch == "master" or state.branch == "main"
    assert state.head
    assert state.clean is False
    assert state.next_steps[0]["plan_id"] == plan.id
    assert state.unresolved_tasks[0]["content"] == "follow up"
