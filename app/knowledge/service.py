from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from app.knowledge.embeddings import EmbeddingProvider
from app.knowledge.models import (
    Evidence,
    KnowledgeChunk,
    KnowledgeIndexResult,
    KnowledgeSearchResult,
)
from app.knowledge.store import KnowledgeStore
from app.policy.workspaces import WorkspaceRegistry

SOURCE_EXTENSIONS = {
    ".py": ("python", "source_code"),
    ".md": ("markdown", "markdown"),
    ".markdown": ("markdown", "markdown"),
    ".txt": ("text", "text"),
    ".json": ("json", "json"),
    ".yaml": ("yaml", "yaml"),
    ".yml": ("yaml", "yaml"),
}
IGNORED_DIRECTORIES = {
    ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv",
    "__pycache__", "build", "dist", "node_modules", "venv",
}
MAX_STRUCTURAL_LINES = 200
EMBEDDING_REPRESENTATION_VERSION = "knowledge-chunk-structure-v1"


def embedding_text(chunk: KnowledgeChunk) -> str:
    lines = [f"file: {chunk.file_path}", f"language: {chunk.language}"]
    if chunk.symbol_name:
        lines.append(f"symbol: {chunk.symbol_name}")
    if chunk.symbol_type:
        lines.append(f"symbol_type: {chunk.symbol_type}")
    if chunk.parent_symbol:
        lines.append(f"parent_symbol: {chunk.parent_symbol}")
    lines.extend(("source:", chunk.content))
    return "\n".join(lines)


class KnowledgeService:
    def __init__(
        self,
        workspaces: WorkspaceRegistry,
        store: KnowledgeStore,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._workspaces = workspaces
        self._store = store
        self._embedding_provider = embedding_provider

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
            source_info = SOURCE_EXTENSIONS.get(path.suffix.casefold())
            if source_info is None:
                continue
            language, source_type = source_info
            try:
                content = path.read_text(encoding="utf-8")
                if "\x00" in content:
                    skipped_files += 1
                    continue
                if source_type == "source_code":
                    file_chunks = self._python_chunks(workspace, root, path, content, git_commit_sha)
                else:
                    if source_type == "json":
                        json.loads(content)
                    file_chunks = self._text_chunks(workspace, root, path, content, language, source_type, git_commit_sha)
            except (OSError, UnicodeError, SyntaxError, json.JSONDecodeError):
                skipped_files += 1
                continue
            indexed_files += 1
            chunks.extend(file_chunks)

        self._store.replace_workspace(workspace, chunks)
        self._index_embeddings(chunks)
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
        mode: str = "lexical",
    ) -> list[KnowledgeSearchResult]:
        self._workspaces.get_root(workspace)
        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50")
        if mode not in {"lexical", "semantic", "hybrid"}:
            raise ValueError("mode must be lexical, semantic, or hybrid")
        query_tokens = self._tokens(query)
        if not query_tokens:
            return []

        chunks = self._store.list_workspace(workspace)
        lexical = self._lexical_ranked(chunks, query, query_tokens)
        semantic = self._semantic_ranked(chunks, query) if mode != "lexical" else []
        if mode == "lexical":
            ranked = [(score, chunk, "lexical") for score, chunk in lexical]
        elif mode == "semantic":
            ranked = [(score, chunk, "semantic") for score, chunk in semantic]
        else:
            ranked = self._hybrid_ranked(lexical, semantic, query)

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
                    retrieval_method=retrieval_method,
                    score=score,
                    rank=rank,
                    freshness=self._freshness(chunk),
                ),
            )
            for rank, (score, chunk, retrieval_method) in enumerate(ranked[:limit], start=1)
        ]

    def _index_embeddings(self, chunks: list[KnowledgeChunk]) -> None:
        provider = self._embedding_provider
        if provider is None or not chunks:
            return
        existing = self._store.list_embeddings([chunk.chunk_id for chunk in chunks])
        reusable: list[dict] = []
        missing: list[KnowledgeChunk] = []
        expected_dimensions = provider.dimensions
        for chunk in chunks:
            record = existing.get(chunk.chunk_id)
            if record and record["content_hash"] == chunk.content_hash and record["provider_identity"] == self._embedding_identity(provider) and (not expected_dimensions or record["dimensions"] == expected_dimensions):
                reusable.append(record)
            else:
                missing.append(chunk)
        if missing:
            vectors = provider.embed([embedding_text(chunk) for chunk in missing])
            dimensions = len(vectors[0]) if vectors else 0
            if len(vectors) != len(missing) or dimensions == 0 or any(len(vector) != dimensions for vector in vectors):
                raise RuntimeError("Embedding provider returned invalid vectors")
            reusable.extend({"chunk_id": chunk.chunk_id, "content_hash": chunk.content_hash, "provider_identity": self._embedding_identity(provider), "dimensions": dimensions, "vector": vector} for chunk, vector in zip(missing, vectors, strict=True))
        records = [item if "vector" in item else {**item, "vector": json.loads(item["vector_json"])} for item in reusable]
        self._store.upsert_embeddings(records)

    def _lexical_ranked(self, chunks: list[KnowledgeChunk], query: str, query_tokens: set[str]) -> list[tuple[float, KnowledgeChunk]]:
        ranked = [(self._score(chunk, query, query_tokens), chunk) for chunk in chunks]
        ranked = [item for item in ranked if item[0] > 0]
        ranked.sort(key=lambda item: (-item[0], item[1].file_path, item[1].line_start, item[1].chunk_id))
        return ranked

    def _semantic_ranked(self, chunks: list[KnowledgeChunk], query: str) -> list[tuple[float, KnowledgeChunk]]:
        provider = self._embedding_provider
        if provider is None:
            return []
        query_vector = provider.embed([query])[0]
        records = self._store.list_embeddings([chunk.chunk_id for chunk in chunks])
        ranked: list[tuple[float, KnowledgeChunk]] = []
        for chunk in chunks:
            record = records.get(chunk.chunk_id)
            if not record or record["content_hash"] != chunk.content_hash or record["provider_identity"] != self._embedding_identity(provider):
                continue
            vector = json.loads(record["vector_json"])
            if record["dimensions"] != len(query_vector) or len(vector) != len(query_vector):
                continue
            ranked.append((self._cosine(query_vector, vector), chunk))
        ranked.sort(key=lambda item: (-item[0], item[1].file_path, item[1].line_start, item[1].chunk_id))
        return ranked

    @staticmethod
    def _embedding_identity(provider: EmbeddingProvider) -> str:
        return f"{provider.identity}|representation:{EMBEDDING_REPRESENTATION_VERSION}"

    @staticmethod
    def _hybrid_ranked(lexical: list[tuple[float, KnowledgeChunk]], semantic: list[tuple[float, KnowledgeChunk]], query: str) -> list[tuple[float, KnowledgeChunk, str]]:
        k = 60.0
        fused: dict[str, tuple[float, KnowledgeChunk, bool]] = {}
        for rank, (_, chunk) in enumerate(lexical, start=1):
            fused[chunk.chunk_id] = (1.0 / (k + rank), chunk, query.casefold() in chunk.content.casefold())
        for rank, (_, chunk) in enumerate(semantic, start=1):
            prior = fused.get(chunk.chunk_id)
            score = prior[0] if prior else 0.0
            exact = prior[2] if prior else False
            fused[chunk.chunk_id] = (score + 1.0 / (k + rank), chunk, exact)
        ranked = [(score + (1.0 if exact else 0.0), chunk, "hybrid") for score, chunk, exact in fused.values()]
        ranked.sort(key=lambda item: (-item[0], item[1].file_path, item[1].line_start, item[1].chunk_id))
        return ranked

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)

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

    def _text_chunks(
        self, workspace: str, root: Path, path: Path, content: str,
        language: str, source_type: str, git_commit_sha: str | None,
    ) -> list[KnowledgeChunk]:
        lines = content.splitlines(keepends=True)
        if not lines:
            return []
        sections: list[tuple[int, int, str | None]] = []
        if source_type == "markdown":
            starts = [(index, line.lstrip()[len(line.lstrip()) - len(line.lstrip("#")):].strip() or None)
                      for index, line in enumerate(lines, 1) if line.lstrip().startswith("#")]
            boundaries = [start for start, _ in starts]
            for position, start in enumerate(boundaries):
                end = boundaries[position + 1] - 1 if position + 1 < len(boundaries) else len(lines)
                sections.append((start, end, next(title for line_no, title in starts if line_no == start)))
            if not sections:
                sections = [(1, len(lines), None)]
        elif source_type == "text":
            start = 1
            for index, line in enumerate(lines, 1):
                if not line.strip() and start < index:
                    sections.append((start, index - 1, None))
                    start = index + 1
            if start <= len(lines):
                sections.append((start, len(lines), None))
        else:
            sections = [(1, len(lines), None)]
        chunks: list[KnowledgeChunk] = []
        for start, end, heading in sections:
            for offset in range(start, end + 1, MAX_STRUCTURAL_LINES):
                chunk_end = min(end, offset + MAX_STRUCTURAL_LINES - 1)
                chunk_content = "".join(lines[offset - 1:chunk_end])
                content_hash = hashlib.sha256(chunk_content.encode("utf-8")).hexdigest()
                file_path = path.relative_to(root).as_posix()
                identity = "\n".join((workspace, file_path, str(offset), str(chunk_end), content_hash))
                chunks.append(KnowledgeChunk(
                    chunk_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(), content=chunk_content,
                    source_type=source_type, workspace=workspace, file_path=file_path, language=language,
                    line_start=offset, line_end=chunk_end, content_hash=content_hash,
                    git_commit_sha=git_commit_sha, indexed_at=datetime.now(UTC),
                    metadata={"heading": heading} if heading else {},
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
