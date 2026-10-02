from pathlib import Path

from app.config import Settings
from app.policy.factory import build_workspace_registry


def test_build_workspace_registry() -> None:
    settings = Settings(
        _env_file=None,
        seo_crawler_workspace=r"D:\Test\SEO",
        knowledge_workspace=r"D:\Test\Knowledge",
        acceptance_test_workspace=r"D:\Test\Acceptance",
    )

    registry = build_workspace_registry(settings)

    assert registry.names() == [
        "acceptance_test",
        "knowledge",
        "rexroad_ai_journal",
        "seo_crawler",
    ]

    assert registry.get_root(
        "seo_crawler"
    ) == Path(r"D:\Test\SEO").resolve()

    assert registry.get_root(
        "knowledge"
    ) == Path(r"D:\Test\Knowledge").resolve()

    assert registry.get_root(
        "acceptance_test"
    ) == Path(r"D:\Test\Acceptance").resolve()
