from __future__ import annotations

import ast
import hashlib
import subprocess
from pathlib import Path
from typing import Any

from app.knowledge.store import KnowledgeStore
from app.policy.workspaces import WorkspaceRegistry

IGNORED = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", "dist", "build"}
SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".php"}
MAX_ITEMS = 100


class WorkspaceNavigator:
    """Deterministic, read-only repository orientation over live workspace files."""

    def __init__(self, workspaces: WorkspaceRegistry, knowledge: KnowledgeStore | None = None) -> None:
        self._workspaces = workspaces
        self._knowledge = knowledge

    def navigate(self, workspace: str, operation: str = "map", query: str = "", relative_path: str = ".") -> dict[str, Any]:
        root = self._workspaces.get_root(workspace)
        if not root.is_dir():
            raise ValueError("workspace is unavailable")
        operation = operation.strip().lower()
        if operation == "map": return self._map(workspace, root)
        if operation in {"symbol", "find_symbol"}: return self._symbols(workspace, root, query)
        if operation == "related": return self._related(workspace, root, query)
        if operation in {"tests", "test"}: return self._tests(workspace, root, query)
        if operation in {"changed", "git"}: return self._git(workspace, root)
        if operation in {"entry_points", "entrypoints"}: return self._entry_points(workspace, root)
        raise ValueError("unsupported navigation operation")

    def _map(self, workspace: str, root: Path) -> dict[str, Any]:
        files = self._files(root)
        top = sorted({path.relative_to(root).parts[0] for path in files if path.relative_to(root).parts})[:MAX_ITEMS]
        key_files = [name for name in ("README.md", "pyproject.toml", "requirements.txt", "package.json", "Makefile") if (root / name).is_file()]
        indexed = self._knowledge.list_workspace(workspace) if self._knowledge is not None else []
        visible = [{"file": path.relative_to(root).as_posix(), "kind": "test" if self._is_test(path) else "source" if path.suffix.lower() in SOURCE_SUFFIXES else "support", "fresh": self._fresh(path)} for path in files[:MAX_ITEMS]]
        symbols = self._symbols(workspace, root, "")["matches"][:MAX_ITEMS]
        tests = [item["file"] for item in visible if item["kind"] == "test"]
        return {"workspace": workspace, "operation": "map", "top_level": top, "key_files": key_files, "files": visible, "tests": tests, "symbols": symbols, "file_count": len(files), "source_file_count": sum(path.suffix.lower() in SOURCE_SUFFIXES for path in files), "test_file_count": sum(self._is_test(path) for path in files), "indexed_hint_count": len(indexed), "git": self._git(workspace, root)["git"]}

    def _symbols(self, workspace: str, root: Path, query: str) -> dict[str, Any]:
        tokens = [token for token in query.strip().lower().replace("_", " ").split() if token]; matches = []
        for path in self._files(root):
            if path.suffix.lower() != ".py": continue
            try: tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError, UnicodeError): continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and (not tokens or any(token in node.name.lower() for token in tokens)):
                    matches.append(self._symbol(path, root, node))
        return {"workspace": workspace, "operation": "find_symbol", "query": query[:200], "matches": sorted(matches, key=lambda item: (item["file"], item["line"], item["name"]))[:MAX_ITEMS], "truncated": len(matches) > MAX_ITEMS}

    def _related(self, workspace: str, root: Path, query: str) -> dict[str, Any]:
        needle = query.strip().lower(); results = []
        for path in self._files(root):
            rel = path.relative_to(root).as_posix()
            if needle and needle not in rel.lower() and needle not in path.stem.lower(): continue
            results.append({"file": rel, "kind": "test" if self._is_test(path) else "source" if path.suffix.lower() in SOURCE_SUFFIXES else "support", "fresh": self._fresh(path)})
        return {"workspace": workspace, "operation": "related", "query": query[:200], "files": results[:MAX_ITEMS], "truncated": len(results) > MAX_ITEMS}

    def _tests(self, workspace: str, root: Path, query: str) -> dict[str, Any]:
        needle = query.strip().lower(); tests = []
        for path in self._files(root):
            if not self._is_test(path): continue
            try: text = path.read_text(encoding="utf-8", errors="ignore")[:20000].lower()
            except OSError: text = ""
            if needle and needle not in path.name.lower() and needle not in text: continue
            tests.append({"file": path.relative_to(root).as_posix(), "fresh": self._fresh(path)})
        return {"workspace": workspace, "operation": "tests", "query": query[:200], "tests": tests[:MAX_ITEMS], "truncated": len(tests) > MAX_ITEMS}

    def _entry_points(self, workspace: str, root: Path) -> dict[str, Any]:
        names = {"main.py", "__main__.py", "app.py", "cli.py", "manage.py", "dockerfile", "pyproject.toml", "package.json"}
        files = [{"file": path.relative_to(root).as_posix(), "fresh": self._fresh(path)} for path in self._files(root) if path.name.lower() in names]
        return {"workspace": workspace, "operation": "entry_points", "files": files[:MAX_ITEMS]}

    def _git(self, workspace: str, root: Path) -> dict[str, Any]:
        def run(*args: str) -> str:
            try:
                result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False, timeout=5)
                return result.stdout.strip()[:4000] if result.returncode == 0 else ""
            except (OSError, subprocess.SubprocessError): return ""
        git_root = run("rev-parse", "--show-toplevel")
        if not git_root or Path(git_root).resolve() != root.resolve():
            return {"workspace": workspace, "operation": "git", "git": {"available": False, "branch": "", "head": "", "changed_files": []}}
        return {"workspace": workspace, "operation": "git", "git": {"available": True, "branch": run("branch", "--show-current"), "head": run("rev-parse", "HEAD"), "changed_files": [line[3:].strip() for line in run("status", "--short").splitlines() if len(line) > 3][:MAX_ITEMS]}}

    def _files(self, root: Path) -> list[Path]:
        return [path for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()) if path.is_file() and not any(part in IGNORED for part in path.relative_to(root).parts)]

    @staticmethod
    def _is_test(path: Path) -> bool: return path.name.startswith("test_") or path.name.endswith("_test.py") or "tests" in path.parts

    @staticmethod
    def _symbol(path: Path, root: Path, node: ast.AST) -> dict[str, Any]: return {"file": path.relative_to(root).as_posix(), "name": node.name, "type": "class" if isinstance(node, ast.ClassDef) else "function", "line": getattr(node, "lineno", 0), "end_line": getattr(node, "end_lineno", getattr(node, "lineno", 0))}

    @staticmethod
    def _fresh(path: Path) -> dict[str, Any]:
        try:
            content = path.read_bytes(); return {"content_hash": hashlib.sha256(content).hexdigest(), "bytes": len(content), "live": True}
        except OSError: return {"content_hash": None, "bytes": 0, "live": False}
