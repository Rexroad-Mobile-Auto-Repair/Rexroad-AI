from __future__ import annotations

import subprocess
from pathlib import Path

from app.policy.workspaces import WorkspaceRegistry


class GitCommandError(RuntimeError):
    pass


class ReadOnlyGit:
    def __init__(self, workspaces: WorkspaceRegistry) -> None:
        self._workspaces = workspaces

    def _run(
        self,
        workspace: str,
        args: list[str],
    ) -> str:
        root = self._workspaces.get_root(workspace)

        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            raise GitCommandError(
                result.stderr.strip()
                or f"Git command failed with exit code {result.returncode}"
            )

        return result.stdout.strip()

    def status(self, workspace: str) -> str:
        return self._run(
            workspace,
            ["status", "--short", "--branch"],
        )

    def branch(self, workspace: str) -> str:
        return self._run(
            workspace,
            ["branch", "--show-current"],
        )

    def diff(
        self,
        workspace: str,
        ref: str | None = None,
    ) -> str:
        args = ["diff"]

        if ref:
            args.append(ref)

        return self._run(
            workspace,
            args,
        )

    def log(
        self,
        workspace: str,
        limit: int = 10,
    ) -> str:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")

        return self._run(
            workspace,
            [
                "log",
                f"-{limit}",
                "--oneline",
                "--decorate",
            ],
        )

    def show(
        self,
        workspace: str,
        ref: str = "HEAD",
    ) -> str:
        return self._run(
            workspace,
            [
                "show",
                "--stat",
                "--oneline",
                "--decorate",
                ref,
            ],
        )
