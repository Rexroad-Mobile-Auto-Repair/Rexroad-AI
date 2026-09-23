from __future__ import annotations

from app.knowledge.models import KnowledgeSearchResult
from app.knowledge.service import KnowledgeService
from app.policy.workspaces import WorkspaceRegistry


class CrossWorkspaceKnowledgeService:
    def __init__(self, workspaces: WorkspaceRegistry, knowledge: KnowledgeService) -> None:
        self._workspaces = workspaces
        self._knowledge = knowledge

    def search_across_workspaces(
        self,
        workspaces: list[str],
        query: str,
        limit: int = 20,
        mode: str = "lexical",
    ) -> list[KnowledgeSearchResult]:
        if not workspaces or len(set(workspaces)) != len(workspaces) or limit < 1 or limit > 100:
            raise ValueError("explicit unique workspaces and bounded limit are required")
        for workspace in workspaces:
            self._workspaces.get_root(workspace)
        results: list[KnowledgeSearchResult] = []
        per_workspace_limit = min(limit, 100)
        for workspace in workspaces:
            results.extend(self._knowledge.search(workspace, query, per_workspace_limit, mode))
        results.sort(key=lambda item: (-item.evidence.score, item.evidence.rank, item.chunk.workspace, item.chunk.file_path, item.chunk.line_start, item.chunk.chunk_id))
        return results[:limit]
