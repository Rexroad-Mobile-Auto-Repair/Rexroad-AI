from pathlib import Path

from app.config import Settings
from app.policy.factory import build_workspace_registry


def test_build_workspace_registry() -> None:
    settings = Settings(
        _env_file=None,
        seo_crawler_workspace=r"D:\Test\SEO",
        knowledge_workspace=r"D:\Test\Knowledge",
    )

    registry = build_workspace_registry(settings)

    assert registry.names() == [
        "knowledge",
        "seo_crawler",
    ]

    assert registry.get_root(
        "seo_crawler"
    ) == Path(r"D:\Test\SEO").resolve()

    assert registry.get_root(
        "knowledge"
    ) == Path(r"D:\Test\Knowledge").resolve()
