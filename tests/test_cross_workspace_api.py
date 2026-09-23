import asyncio

from app import main


def test_cross_workspace_api_requires_explicit_allowlist(monkeypatch):
    class Fake:
        seen = None
        def search_across_workspaces(self, workspaces, query, limit, mode):
            self.seen = (workspaces, query, limit, mode)
            return []

    monkeypatch.setattr(main, "cross_workspace_service", Fake())
    asyncio.run(main.search_across_workspaces(["a", "b"], "x"))
    assert main.cross_workspace_service.seen == (["a", "b"], "x", 20, "lexical")
