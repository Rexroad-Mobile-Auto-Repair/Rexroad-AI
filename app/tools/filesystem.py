from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.policy.workspaces import WorkspaceRegistry


class ReadOnlyFilesystem:
    def __init__(self, workspaces: WorkspaceRegistry) -> None:
        self._workspaces = workspaces

    def list(
        self,
        workspace: str,
        relative_path: str = ".",
    ) -> list[str]:
        path = self._workspaces.resolve_path(
            workspace,
            relative_path,
        )

        if not path.is_dir():
            raise NotADirectoryError(str(path))

        return sorted(
            item.name
            for item in path.iterdir()
        )

    def read(
        self,
        workspace: str,
        relative_path: str,
    ) -> str:
        path = self._workspaces.resolve_path(
            workspace,
            relative_path,
        )

        if not path.is_file():
            raise FileNotFoundError(str(path))

        return path.read_text(
            encoding="utf-8",
            errors="replace",
        )

    def search(
        self,
        workspace: str,
        query: str,
        relative_path: str = ".",
    ) -> list[str]:
        root = self._workspaces.resolve_path(
            workspace,
            relative_path,
        )

        if not root.is_dir():
            raise NotADirectoryError(str(root))

        matches: list[str] = []
        normalized_query = query.casefold()

        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue

            try:
                content = path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                continue

            if normalized_query in content.casefold():
                matches.append(
                    str(
                        path.relative_to(
                            self._workspaces.get_root(workspace)
                        )
                    )
                )

        return matches

    def apply_patch(self, workspace: str, relative_path: str, expected_text: str, replacement: str) -> dict[str, Any]:
        if len(expected_text.encode()) > 64_000 or len(replacement.encode()) > 64_000:
            raise ValueError("patch payload exceeds limit")
        candidate_path = Path(relative_path)
        if candidate_path.is_absolute() or ".." in candidate_path.parts:
            raise PermissionError("path must remain workspace-relative")
        root = self._workspaces.get_root(workspace)
        path = self._workspaces.resolve_path(workspace, relative_path)
        if path == root or not path.is_file():
            raise FileNotFoundError("target file not found")
        status = subprocess.run(["git", "status", "--short", "--untracked-files=all", "--", relative_path], cwd=root, capture_output=True, text=True, check=False)
        if status.returncode != 0 or status.stdout.strip():
            raise ValueError("target file has uncommitted changes")
        current = path.read_text(encoding="utf-8")
        if current != expected_text:
            raise ValueError("expected file content does not match")
        before = hashlib.sha256(current.encode()).hexdigest()
        after_text = current.replace(expected_text, replacement, 1)
        after = hashlib.sha256(after_text.encode()).hexdigest()
        if before == after:
            return {"workspace": workspace, "relative_path": relative_path, "changed": False, "before_hash": before, "after_hash": after, "bytes_changed": 0}
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = handle.name
            handle.write(after_text)
        try:
            os.replace(temporary, path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise
        return {"workspace": workspace, "relative_path": relative_path, "changed": True, "before_hash": before, "after_hash": after, "bytes_changed": len(after_text.encode()) - len(current.encode())}
