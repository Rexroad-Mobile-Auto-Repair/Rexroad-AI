from pathlib import Path

from app.config import Settings
from app.policy.workspaces import WorkspaceRegistry


def build_workspace_registry(
    settings: Settings,
) -> WorkspaceRegistry:
    return WorkspaceRegistry(
        {
            "seo_crawler": Path(settings.seo_crawler_workspace),
            "knowledge": Path(settings.knowledge_workspace),
            "acceptance_test": Path(settings.acceptance_test_workspace),
            "rexroad_ai_journal": Path(settings.rexroad_ai_journal_workspace),
        }
    )
