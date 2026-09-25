from __future__ import annotations

from pydantic import BaseModel


class WorkerProfile(BaseModel):
    worker_type: str
    display_name: str
    description: str
    allowed_task_categories: frozenset[str]
    preferred_tools: tuple[str, ...] = ()
    read_only: bool = True
    max_result_size: int = 4000


PROFILES: dict[str, WorkerProfile] = {
    "code_analyst": WorkerProfile(worker_type="code_analyst", display_name="Code analyst", description="Inspects implementation and symbols.", allowed_task_categories=frozenset({"read", "repo_map", "navigation"}), preferred_tools=("workspace.repo_map",)),
    "test_analyst": WorkerProfile(worker_type="test_analyst", display_name="Test analyst", description="Inspects tests and coverage gaps.", allowed_task_categories=frozenset({"read", "repo_map", "navigation"}), preferred_tools=("workspace.repo_map",)),
    "architecture_analyst": WorkerProfile(worker_type="architecture_analyst", display_name="Architecture analyst", description="Inspects module relationships and entry points.", allowed_task_categories=frozenset({"read", "repo_map", "navigation"}), preferred_tools=("workspace.repo_map",)),
    "security_analyst": WorkerProfile(worker_type="security_analyst", display_name="Security analyst", description="Inspects policy and approval boundaries.", allowed_task_categories=frozenset({"read", "repo_map", "navigation"}), preferred_tools=("workspace.repo_map",)),
    "researcher": WorkerProfile(worker_type="researcher", display_name="Researcher", description="Performs bounded approved research.", allowed_task_categories=frozenset({"research", "read"}), read_only=True),
    "verifier": WorkerProfile(worker_type="verifier", display_name="Verifier", description="Performs independent validation.", allowed_task_categories=frozenset({"read", "execute", "repo_map"}), preferred_tools=("workspace.repo_map",)),
}


def get_profile(worker_type: str) -> WorkerProfile:
    try:
        return PROFILES[worker_type]
    except KeyError as exc:
        raise ValueError("unknown worker profile") from exc
