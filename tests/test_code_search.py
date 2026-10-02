import pytest
from fastapi.testclient import TestClient

from app import main
from app.policy.workspaces import WorkspaceAccessError, WorkspaceRegistry
from app.tools.code_search import MAX_FILE_BYTES, WorkspaceCodeSearch
from app.tools.factory import build_tool_registry
from app.tools.filesystem import ReadOnlyFilesystem
from app.tools.git import ReadOnlyGit


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "example.py").write_text(
        'def greeting(name):\n    return f"Hello, {name}"\n', encoding="utf-8"
    )
    (root / "tests/test_example.py").write_text(
        'def test_greeting():\n    assert greeting("José") == "Hello, José"\n', encoding="utf-8"
    )
    registry = WorkspaceRegistry({"project": root})
    return root, registry, WorkspaceCodeSearch(registry)


def test_literal_search_has_real_lines_context_and_filename_filters(project):
    root, _, search = project
    before = (root / "example.py").read_bytes()
    result = search.grep("project", "GREETING", pattern="example.py")
    assert result["matches"] == [
        {
            "file": "example.py",
            "line": 1,
            "text": "def greeting(name):",
            "context": [{"line": 2, "text": '    return f"Hello, {name}"'}],
        }
    ]
    assert search.grep("project", "GREETING", ignore_case=False)["matches"] == []
    assert search.grep("project", "José", pattern="tests/*.py")["matches"][0]["line"] == 2
    assert search.grep("project", "greet.*")["matches"] == []
    assert (root / "example.py").read_bytes() == before


def test_glob_finds_root_and_nested_source_files(project):
    _, _, search = project
    assert search.glob("project", "**/*.py")["files"] == ["example.py", "tests/test_example.py"]
    assert search.glob("project", "tests/**/*.py")["files"] == ["tests/test_example.py"]
    assert search.glob("project", "*.php")["files"] == []


def test_generated_private_oversized_and_binary_files_are_excluded(project):
    root, _, search = project
    for folder in [".venv", ".git", ".pytest-tmp-extra", "node_modules"]:
        (root / folder).mkdir()
        (root / folder / "hidden.py").write_text("SECRET_MATCH", encoding="utf-8")
    (root / ".env.txt").write_text("SECRET_MATCH", encoding="utf-8")
    (root / "credentials.json").write_text("SECRET_MATCH", encoding="utf-8")
    (root / "binary.py").write_bytes(b"SECRET_MATCH\x00")
    (root / "large.py").write_text("SECRET_MATCH" + "x" * MAX_FILE_BYTES, encoding="utf-8")
    result = search.grep("project", "SECRET_MATCH")
    assert result["matches"] == [] and result["skipped_files"] == 2
    files = search.glob("project")["files"]
    assert not any("hidden" in file or "credentials" in file or ".env" in file for file in files)
    with pytest.raises(WorkspaceAccessError):
        search.grep("project", "SECRET_MATCH", relative_path=".venv")


def test_result_limits_report_partial_search(project):
    _, _, search = project
    files = search.glob("project", limit=1)
    assert files["files"] == ["example.py"] and files["truncated"] is True
    matches = search.grep("project", "greeting", limit=1, context=0)
    assert len(matches["matches"]) == 1 and matches["truncated"] is True


def test_search_rejects_workspace_escape_and_invalid_limits(project):
    _, _, search = project
    with pytest.raises(WorkspaceAccessError):
        search.grep("project", "text", relative_path="..")
    with pytest.raises(WorkspaceAccessError):
        search.glob("unknown")
    for pattern in ["../*.py", "C:\\outside\\*.py"]:
        with pytest.raises(ValueError):
            search.glob("project", pattern)
    for limit in [0, 51, True]:
        with pytest.raises(ValueError):
            search.glob("project", limit=limit)


def test_search_skips_symlink_to_another_workspace(project, tmp_path):
    root, _, search = project
    outside = tmp_path / "outside.py"
    outside.write_text("OUTSIDE_ONLY", encoding="utf-8")
    try:
        (root / "link.py").symlink_to(outside)
    except OSError:
        pytest.skip("Host cannot create test symlinks")
    assert "link.py" not in search.glob("project")["files"]
    assert search.grep("project", "OUTSIDE_ONLY")["matches"] == []


def test_read_only_tools_and_api_share_the_same_search(project, monkeypatch):
    _, registry, search = project
    tools = build_tool_registry(ReadOnlyFilesystem(registry), ReadOnlyGit(registry))
    assert (
        tools.get("filesystem.grep").permission == tools.get("filesystem.glob").permission == "read"
    )
    expected = tools.execute(
        "filesystem.grep", workspace="project", query="greeting", pattern="example.py"
    )
    monkeypatch.setattr(main, "code_search", search)
    client = TestClient(main.app)
    assert (
        client.get(
            "/workspaces/project/search", params={"query": "greeting", "pattern": "example.py"}
        ).json()
        == expected
    )
    assert (
        client.get("/workspaces/project/search", params={"pattern": "*.py"}).json()["mode"]
        == "files"
    )
    assert client.get("/workspaces/unknown/search").status_code == 404
    assert client.get("/workspaces/project/search", params={"limit": 51}).status_code == 422
    assert (
        client.get("/workspaces/project/search", params={"pattern": "../*.py"}).status_code == 400
    )


def test_operator_exposes_search_and_safe_text_renderer():
    client = TestClient(main.app)
    page = client.get("/operator").text
    assert "Search project" in page and "/project-search.js" in page
    asset = client.get("/project-search.js")
    assert asset.status_code == 200 and "textContent" in asset.text
    assert "innerHTML" not in asset.text and "method: 'POST'" not in asset.text
