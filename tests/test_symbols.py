import pytest
from fastapi.testclient import TestClient

from app import main
from app.policy.workspaces import WorkspaceRegistry
from app.tools.code_search import WorkspaceCodeSearch
from app.tools.factory import build_tool_registry
from app.tools.filesystem import ReadOnlyFilesystem
from app.tools.git import ReadOnlyGit
from app.tools.symbols import PythonSymbols


@pytest.fixture
def symbols(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    content = 'def greeting(name):\n    return name\n# greeting("comment")\ntext = "greeting(string)"\ngreeting("real")\nobj.greeting()\nasync def greeting():\n    pass\nclass Greeter:\n    def greeting(self):\n        pass\n'
    (root / "example.py").write_text(content, encoding="utf-8")
    (root / "invalid.py").write_text("def broken(", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv/hidden.py").write_text("def greeting(): pass", encoding="utf-8")
    registry = WorkspaceRegistry({"project": root})
    search = WorkspaceCodeSearch(registry)
    monkeypatch.setattr(main, "code_search", search)
    return root, registry, PythonSymbols(search), TestClient(main.app)


def test_definitions_and_possible_references_exclude_comments_strings(symbols):
    root, _, service, _ = symbols
    before = (root / "example.py").read_bytes()
    definitions = service.find("project", "greeting")
    assert [m["line"] for m in definitions["matches"]] == [1, 7, 10]
    assert definitions["skipped_files"] == 1
    references = service.find("project", "greeting", "references")
    assert [m["line"] for m in references["matches"]] == [5, 6]
    assert references["binding_resolved"] is False
    assert all(m["kind"] == "possible_reference" for m in references["matches"])
    assert service.find("project", "Greeter")["matches"][0]["kind"] == "class"
    assert (root / "example.py").read_bytes() == before


def test_symbol_filters_limits_unicode_and_import_candidates(symbols):
    root, _, service, _ = symbols
    (root / "other.py").write_text(
        'from source import greeting as hello\ndef café(): pass\nf"{greeting()}"\n',
        encoding="utf-8",
    )
    assert service.find("project", "café")["matches"][0]["line"] == 2
    result = service.find("project", "greeting", limit=1)
    assert len(result["matches"]) == 1 and result["truncated"]
    assert (
        len(service.find("project", "greeting", "references", pattern="other.py")["matches"]) == 2
    )
    assert service.find("project", "greeting", pattern="*.php")["matches"] == []


def test_symbol_api_tool_boundaries_and_source_link(symbols):
    _, registry, _, client = symbols
    tools = build_tool_registry(ReadOnlyFilesystem(registry), ReadOnlyGit(registry))
    assert tools.get("workspace.symbols").permission == "read"
    expected = tools.execute("workspace.symbols", workspace="project", symbol="greeting")
    assert (
        client.get("/workspaces/project/symbols", params={"symbol": "greeting"}).json() == expected
    )
    match = expected["matches"][0]
    assert (
        client.get(
            "/workspaces/project/source", params={"path": match["file"], "line": match["line"]}
        ).json()["selected_line"]
        == match["line"]
    )
    assert (
        client.get("/workspaces/unknown/symbols", params={"symbol": "greeting"}).status_code == 404
    )
    for params in [
        {"symbol": "a.b"},
        {"symbol": "greeting", "kind": "calls"},
        {"symbol": "greeting", "pattern": "../*.py"},
    ]:
        assert client.get("/workspaces/project/symbols", params=params).status_code == 400
    assert (
        client.get(
            "/workspaces/project/symbols", params={"symbol": "greeting", "limit": 51}
        ).status_code
        == 422
    )
