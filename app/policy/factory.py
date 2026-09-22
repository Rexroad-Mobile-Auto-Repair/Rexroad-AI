from pathlib import Path

from app.config import Settings
from app.policy.workspaces import WorkspaceRegistry


def build_workspace_registry(
    settings: Settings,
) -> WorkspaceRegistry:
    return WorkspaceRegistry(
        {
            "seo_crawler": Path(
                settings.seo_crawler_workspace
            ),
            "knowledge": Path(
                settings.knowledge_workspace
            ),
        }
    )
