"""Bounded Rexroad adapter for Clawd-Code Grep/Glob file-search ideas.

File traversal and glob matching adapted from Clawd Codex Team (2026), MIT.
See third_party/clawd_code/LICENSE. Rexroad supplies confinement and scan limits.
This first integration searches literal text, not arbitrary regular expressions.
"""

from __future__ import annotations

import fnmatch
import json
import os
import time
from pathlib import Path

from app.policy.workspaces import WorkspaceAccessError, WorkspaceRegistry

IGNORED_DIRS = {
    ".git",
    ".svn",
    ".hg",
    ".bzr",
    ".jj",
    ".sl",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
}
TEXT_SUFFIXES = {
    ".py",
    ".php",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".css",
    ".html",
    ".md",
    ".txt",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".xml",
    ".ini",
    ".cfg",
    ".ps1",
    ".sql",
}
MAX_FILE_BYTES = 256_000
MAX_READ_BYTES = 8_000_000
MAX_OUTPUT_CHARS = 12_000


def _matches_glob(path: Path, pattern: str, root: Path) -> bool:
    # Adapted from Clawd's Grep helper; expose workspace-relative paths only.
    relative = path.relative_to(root).as_posix()
    patterns = {pattern, pattern.replace("/**/", "/")}
    if pattern.startswith("**/"):
        patterns.add(pattern[3:])
    return any(fnmatch.fnmatch(path.name, p) or fnmatch.fnmatch(relative, p) for p in patterns)


class WorkspaceCodeSearch:
    def __init__(self, workspaces: WorkspaceRegistry) -> None:
        self.workspaces = workspaces

    def _validate(self, workspace, relative_path, pattern, limit):
        if (
            not isinstance(pattern, str)
            or not pattern
            or len(pattern) > 200
            or ".." in Path(pattern).parts
            or Path(pattern).is_absolute()
        ):
            raise ValueError("Use a workspace-relative file pattern of at most 200 characters")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Result limit must be between 1 and 50")
        root = self.workspaces.get_root(workspace)
        base = self.workspaces.resolve_path(workspace, relative_path)
        if not base.is_dir():
            raise ValueError("Search folder is unavailable")
        if any(self._ignored(part) for part in base.relative_to(root).parts):
            raise WorkspaceAccessError("Search folder is excluded")
        return root, base

    @staticmethod
    def _ignored(name):
        return name.casefold() in IGNORED_DIRS or name.casefold().startswith(".pytest-tmp")

    def _files(self, workspace, root, base, pattern, stats):
        started = time.monotonic()
        visited = 0
        # Adapted from Clawd's _iter_files: prune before descending, never follow links.
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = sorted(
                d
                for d in dirnames
                if not self._ignored(d)
                and not (Path(dirpath) / d).is_symlink()
                and (Path(dirpath) / d).resolve().is_relative_to(root)
            )
            for name in sorted(filenames):
                visited += 1
                if visited > 5000 or time.monotonic() - started > 2:
                    stats["scan_truncated"] = True
                    return
                path = Path(dirpath) / name
                if (
                    path.is_symlink()
                    or name.casefold().startswith(".env")
                    or any(
                        word in name.casefold() for word in ("credential", "secret", "private_key")
                    )
                    or path.suffix.casefold() not in TEXT_SUFFIXES
                ):
                    continue
                try:
                    allowed = self.workspaces.resolve_path(
                        workspace, path.relative_to(root).as_posix()
                    )
                    if allowed.is_file() and _matches_glob(path, pattern, root):
                        yield allowed
                except (OSError, WorkspaceAccessError):
                    continue

    def glob(
        self, workspace: str, pattern: str = "**/*", relative_path: str = ".", limit: int = 30
    ) -> dict:
        root, base = self._validate(workspace, relative_path, pattern, limit)
        stats = {"scan_truncated": False}
        files = []
        for path in self._files(workspace, root, base, pattern, stats):
            files.append(path.relative_to(root).as_posix())
            if len(files) > limit:
                break
        return {
            "workspace": workspace,
            "mode": "files",
            "pattern": pattern,
            "files": files[:limit],
            "truncated": len(files) > limit or stats["scan_truncated"],
            **stats,
        }

    def grep(
        self,
        workspace: str,
        query: str,
        pattern: str = "**/*",
        relative_path: str = ".",
        ignore_case: bool = True,
        context: int = 1,
        limit: int = 20,
    ) -> dict:
        root, base = self._validate(workspace, relative_path, pattern, limit)
        if not isinstance(query, str) or not query or len(query) > 200:
            raise ValueError("Search text must contain 1 to 200 characters")
        if type(context) is not int or not 0 <= context <= 2 or type(ignore_case) is not bool:
            raise ValueError("Invalid search options")
        stats = {"scan_truncated": False, "scanned_files": 0, "skipped_files": 0}
        matches, read_bytes, output_chars, truncated = [], 0, 0, False
        needle = query.casefold() if ignore_case else query
        for path in self._files(workspace, root, base, pattern, stats):
            try:
                with path.open("rb") as source:
                    data = source.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES or b"\x00" in data:
                    stats["skipped_files"] += 1
                    continue
                read_bytes += len(data)
                if read_bytes > MAX_READ_BYTES:
                    stats["scan_truncated"] = True
                    break
                lines = data.decode("utf-8").splitlines()
            except (OSError, UnicodeError):
                stats["skipped_files"] += 1
                continue
            stats["scanned_files"] += 1
            for index, line in enumerate(lines):
                if needle not in (line.casefold() if ignore_case else line):
                    continue
                item = {
                    "file": path.relative_to(root).as_posix(),
                    "line": index + 1,
                    "text": line[:300],
                    "context": [
                        {"line": n + 1, "text": lines[n][:180]}
                        for n in range(
                            max(0, index - context), min(len(lines), index + context + 1)
                        )
                        if n != index
                    ],
                }
                output_chars += len(json.dumps(item))
                if len(matches) == limit or output_chars > MAX_OUTPUT_CHARS:
                    truncated = True
                    break
                matches.append(item)
            if truncated:
                break
        return {
            "workspace": workspace,
            "mode": "content",
            "query": query,
            "pattern": pattern,
            "matches": matches,
            "truncated": truncated or stats["scan_truncated"],
            **stats,
        }
