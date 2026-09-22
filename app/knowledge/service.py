from __future__ import annotations

import ast
import hashlib
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from app.knowledge.models import (
    Evidence,
    KnowledgeChunk,
    KnowledgeIndexResult,
    KnowledgeSearchResult,
)
from app.knowledge.store import KnowledgeStore
from app.policy.workspaces import WorkspaceRegistry

SOURCE_EXTENSIONS = {".py": "python"}
IGNORED_DIRECTORIES = {
    ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv",
    "__pycache__", "build", "dist", "node_modules", "venv",
}
MAX_STRUCTURAL_LINES = 200


class KnowledgeService:
    def __init__(
        self,
        workspaces: WorkspaceRegistry,
        store: KnowledgeStore,
    ) -> None:
        self._workspaces = workspaces
        self._store = store

    def index(self, workspace: str) -> KnowledgeIndexResult:
        root = self._workspaces.get_root(workspace)
        git_commit_sha = self._git_commit_sha(root)
        chunks: list[KnowledgeChunk] = []
        indexed_files = 0
        skipped_files = 0

        for path in sorted(root.rglob("*")):
            if not self._is_inside_workspace(path, root):
                continue
            if self._is_ignored(path, root) or not path.is_file():
                continue
            language = SOURCE_EXTENSIONS.get(path.suffix.casefold())
            if language is None:
                continue
            try:
                content = path.read_text(encoding="utf-8")
                if "\x00" in content:
                    skipped_files += 1
                    continue
                file_chunks = self._python_chunks(
                    workspace, root, path, content, git_commit_sha
                )
            except (OSError, UnicodeError, SyntaxError):
                skipped_files += 1
                continue
            indexed_files += 1
            chunks.extend(file_chunks)

        self._store.replace_workspace(workspace, chunks)
        return KnowledgeIndexResult(
            workspace=workspace,
            indexed_chunks=len(chunks),
            indexed_files=indexed_files,
            skipped_files=skipped_files,
            git_commit_sha=git_commit_sha,
        )

    def search(
        self,
        workspace: str,
        query: str,
        limit: int = 10,
    ) -> list[KnowledgeSearchResult]:
        self._workspaces.get_root(workspace)
        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50")
        query_tokens = self._tokens(query)
        if not query_tokens:
            return []

        ranked: list[tuple[float, KnowledgeChunk]] = []
        for chunk in self._store.list_workspace(workspace):
            score = self._score(chunk, query, query_tokens)
            if score > 0:
                ranked.append((score, chunk))
        ranked.sort(key=lambda item: (-item[0], item[1].file_path, item[1].line_start, item[1].chunk_id))

        return [
            KnowledgeSearchResult(
                chunk=chunk,
                evidence=Evidence(
                    chunk_id=chunk.chunk_id,
                    workspace=chunk.workspace,
                    file_path=chunk.file_path,
                    line_start=chunk.line_start,
                    line_end=chunk.line_end,
                    symbol_name=chunk.symbol_name,
                    symbol_type=chunk.symbol_type,
                    content_hash=chunk.content_hash,
                    git_commit_sha=chunk.git_commit_sha,
                    score=score,
                    rank=rank,
                    freshness=self._freshness(chunk),
                ),
            )
            for rank, (score, chunk) in enumerate(ranked[:limit], start=1)
        ]

    def _python_chunks(
        self, workspace: str, root: Path, path: Path, content: str, git_commit_sha: str | None
    ) -> list[KnowledgeChunk]:
        tree = ast.parse(content)
        lines = content.splitlines(keepends=True)
        units: list[tuple[int, int, str | None, str | None, str | None]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                units.append((node.lineno, node.end_lineno or node.lineno, node.name, "function", None))
            elif isinstance(node, ast.ClassDef):
                units.append((node.lineno, node.end_lineno or node.lineno, node.name, "class", None))
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        units.append((member.lineno, member.end_lineno or member.lineno, member.name, "method", node.name))
            else:
                units.append((node.lineno, node.end_lineno or node.lineno, None, "module", None))
        if not units:
            units.append((1, max(1, len(lines)), None, "module", None))
        return [
            chunk
            for start, end, name, kind, parent in units
            for chunk in self._make_chunks(workspace, root, path, lines, start, end, name, kind, parent, git_commit_sha)
        ]

    def _make_chunks(
        self, workspace: str, root: Path, path: Path, lines: list[str], start: int, end: int,
        name: str | None, kind: str | None, parent: str | None, git_commit_sha: str | None,
    ) -> list[KnowledgeChunk]:
        chunks: list[KnowledgeChunk] = []
        for chunk_start in range(start, end + 1, MAX_STRUCTURAL_LINES):
            chunk_end = min(end, chunk_start + MAX_STRUCTURAL_LINES - 1)
            chunk_content = "".join(lines[chunk_start - 1:chunk_end])
            content_hash = hashlib.sha256(chunk_content.encode("utf-8")).hexdigest()
            file_path = path.relative_to(root).as_posix()
            identity = "\n".join((workspace, file_path, str(chunk_start), str(chunk_end), content_hash))
            chunks.append(KnowledgeChunk(
                chunk_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(), content=chunk_content,
                workspace=workspace, file_path=file_path, language="python", line_start=chunk_start,
                line_end=chunk_end, symbol_name=name, symbol_type=kind, parent_symbol=parent,
                content_hash=content_hash, git_commit_sha=git_commit_sha, indexed_at=datetime.now(UTC),
            ))
        return chunks

    def _freshness(self, chunk: KnowledgeChunk) -> str:
        try:
            path = self._workspaces.resolve_path(chunk.workspace, chunk.file_path)
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return "missing"
        lines = content.splitlines(keepends=True)
        current = "".join(lines[chunk.line_start - 1:chunk.line_end])
        if hashlib.sha256(current.encode("utf-8")).hexdigest() != chunk.content_hash:
            return "stale"
        current_sha = self._git_commit_sha(self._workspaces.get_root(chunk.workspace))
        if chunk.git_commit_sha is not None and current_sha != chunk.git_commit_sha:
            return "stale"
        return "current"

    @staticmethod
    def _tokens(value: str) -> set[str]:
        expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
        tokens: set[str] = set()
        for token in re.findall(r"[A-Za-z0-9_]+", expanded):
            tokens.add(token.casefold())
            tokens.update(part.casefold() for part in token.split("_") if part)
        return tokens

    def _score(self, chunk: KnowledgeChunk, query: str, query_tokens: set[str]) -> float:
        content_tokens = self._tokens(chunk.content)
        matched = query_tokens & content_tokens
        if not matched:
            return 0.0
        exact = query.casefold() in chunk.content.casefold()
        symbol_match = bool(chunk.symbol_name and query.casefold() == chunk.symbol_name.casefold())
        return float(len(matched) * 10 + (100 if exact else 0) + (100 if symbol_match else 0))

    @staticmethod
    def _is_ignored(path: Path, root: Path) -> bool:
        return any(part.casefold() in IGNORED_DIRECTORIES for part in path.relative_to(root).parts)

    @staticmethod
    def _is_inside_workspace(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            return False
        return True

    @staticmethod
    def _git_commit_sha(root: Path) -> str | None:
        top_level = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if top_level.returncode != 0 or Path(top_level.stdout.strip()).resolve() != root.resolve():
            return None
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None
