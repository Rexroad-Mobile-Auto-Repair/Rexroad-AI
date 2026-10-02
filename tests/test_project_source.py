import pytest
from fastapi.testclient import TestClient

from app import main
from app.policy.workspaces import WorkspaceRegistry
from app.tools.code_search import MAX_FILE_BYTES, WorkspaceCodeSearch


@pytest.fixture
def source_project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    contents = "\n".join(f"row {n}: José <script>alert(1)</script>" for n in range(1, 251))
    (root / "example.py").write_text(contents, encoding="utf-8")
    monkeypatch.setattr(
        main, "code_search", WorkspaceCodeSearch(WorkspaceRegistry({"project": root}))
    )
    return root, TestClient(main.app)


def test_source_window_keeps_exact_lines_and_file_unchanged(source_project):
    root, client = source_project
    before = (root / "example.py").read_bytes()
    data = client.get(
        "/workspaces/project/source", params={"path": "example.py", "line": 96}
    ).json()
    assert (data["start_line"], data["end_line"], data["total_lines"], data["selected_line"]) == (
        56,
        175,
        250,
        96,
    )
    assert data["lines"][40] == {"line": 96, "text": "row 96: José <script>alert(1)</script>"}
    following = client.get(
        "/workspaces/project/source", params={"path": "example.py", "line": 176}
    ).json()
    assert following["end_line"] == 250
    assert (root / "example.py").read_bytes() == before
    assert (
        client.get(
            "/workspaces/project/source", params={"path": "example.py", "line": 251}
        ).status_code
        == 400
    )


@pytest.mark.parametrize(
    "path",
    [
        "../outside.py",
        "C:/outside.py",
        ".venv/private.py",
        ".env.txt",
        "credentials.json",
        "file.exe",
        "missing.py",
    ],
)
def test_source_rejects_escape_excluded_and_missing_files(source_project, path):
    root, client = source_project
    (root / ".venv").mkdir(exist_ok=True)
    for file in [".venv/private.py", ".env.txt", "credentials.json", "file.exe"]:
        (root / file).write_text("DO_NOT_RETURN", encoding="utf-8")
    response = client.get("/workspaces/project/source", params={"path": path})
    assert response.status_code == 404
    assert "DO_NOT_RETURN" not in response.text
    assert str(root) not in response.text


@pytest.mark.parametrize(
    "content",
    [b"a" * (MAX_FILE_BYTES + 1), b"abc\x00def", b"\xff\xfe"],
    ids=["oversized", "binary", "non_utf8"],
)
def test_source_rejects_oversized_binary_and_non_utf8(source_project, content):
    root, client = source_project
    (root / "invalid.py").write_bytes(content)
    assert (
        client.get("/workspaces/project/source", params={"path": "invalid.py"}).status_code == 400
    )


def test_empty_file_and_invalid_requests(source_project):
    root, client = source_project
    (root / "empty.py").write_bytes(b"")
    assert (
        client.get("/workspaces/project/source", params={"path": "empty.py"}).json()["lines"] == []
    )
    assert (
        client.get("/workspaces/unknown/source", params={"path": "example.py"}).status_code == 404
    )
    assert (
        client.get(
            "/workspaces/project/source", params={"path": "example.py", "line": 0}
        ).status_code
        == 422
    )
    assert client.get("/workspaces/project/source").status_code == 422


def test_source_viewer_and_search_use_text_only_rendering(source_project):
    _, client = source_project
    assert "/project-file.js" in client.get("/operator").text
    viewer = client.get("/project-file.js").text
    search = client.get("/project-search.js").text
    assert "textContent" in viewer and "Selected line" in viewer
    assert "innerHTML" not in viewer and "innerHTML" not in search
    assert "project-source" in search and "project-source" in viewer
