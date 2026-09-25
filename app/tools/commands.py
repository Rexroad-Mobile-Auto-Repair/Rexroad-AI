from __future__ import annotations

import os
import subprocess
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.policy.workspaces import WorkspaceRegistry


class CommandRisk(StrEnum):
    READ_ONLY = "read_only"
    CHECK = "check"
    HIGH_IMPACT = "high_impact"
    DENIED = "denied"


class CommandPolicy:
    @staticmethod
    def classify(command: str, args: list[str]) -> CommandRisk:
        executable = Path(command).name.lower()
        lowered = [str(arg).lower() for arg in args]
        if executable in {"powershell", "powershell.exe", "pwsh", "cmd", "cmd.exe", "bash", "sh", "zsh"}:
            return CommandRisk.DENIED
        if executable in {"git"} and lowered and lowered[0] in {"push", "clean", "reset", "restore", "checkout", "switch", "commit", "add", "merge", "rebase", "clone"}:
            return CommandRisk.DENIED
        if executable in {"pip", "pip.exe", "npm", "curl", "curl.exe", "wget", "wget.exe"}:
            return CommandRisk.DENIED
        if executable == "git" and lowered and lowered[0] in {"status", "diff", "log", "show"}:
            return CommandRisk.READ_ONLY
        if executable in {"python", "python.exe", "py"} and lowered[:2] == ["-m", "pytest"]:
            return CommandRisk.CHECK
        if executable in {"ruff", "ruff.exe"} and lowered[:1] == ["check"]:
            return CommandRisk.CHECK
        if executable in {"python", "python.exe", "py"} and lowered[:1] in (["--version"], ["-v"]):
            return CommandRisk.READ_ONLY
        return CommandRisk.DENIED


class CommandRunner:
    MAX_OUTPUT = 20000

    def __init__(self, workspaces: WorkspaceRegistry) -> None:
        self._workspaces = workspaces

    def run_command(self, workspace: str, command: str, args: list[str] | None = None,
                    cwd: str = ".", timeout_seconds: int = 60) -> dict[str, Any]:
        args = list(args or [])
        if not args and command.casefold() in {"git status", "git diff", "git log", "git show", "python --version"}:
            command, *normalized_args = command.split()
            args = normalized_args
        if not command or len(command) > 200 or any(len(str(arg)) > 2000 for arg in args):
            return {"status": "rejected", "reason": "invalid command arguments"}
        risk = CommandPolicy.classify(command, args)
        if risk == CommandRisk.DENIED:
            return {"status": "denied", "risk": risk.value, "reason": "command is not permitted"}
        if not 1 <= timeout_seconds <= 600:
            return {"status": "rejected", "reason": "timeout_seconds must be between 1 and 600"}
        try:
            root = self._workspaces.get_root(workspace)
            resolved = self._workspaces.resolve_path(workspace, cwd)
        except (KeyError, PermissionError, ValueError) as exc:
            return {"status": "rejected", "reason": str(exc)[:300]}
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [command, *args], cwd=resolved, shell=False, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=timeout_seconds,
                env={key: value for key, value in os.environ.items() if key in {"PATH", "SystemRoot", "TEMP", "TMP"}},
                check=False,
            )
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            return {"status": "completed", "risk": risk.value, "workspace": workspace,
                    "cwd": str(resolved.relative_to(root)), "command": command, "args": args,
                    "exit_code": completed.returncode, "timed_out": False,
                    "stdout": stdout[: self.MAX_OUTPUT], "stderr": stderr[: self.MAX_OUTPUT],
                    "stdout_truncated": len(stdout) > self.MAX_OUTPUT, "stderr_truncated": len(stderr) > self.MAX_OUTPUT,
                    "duration_ms": round((time.perf_counter() - started) * 1000)}
        except subprocess.TimeoutExpired as exc:
            return {"status": "timeout", "risk": risk.value, "workspace": workspace, "cwd": cwd,
                    "command": command, "args": args, "timed_out": True,
                    "stdout": str(exc.stdout or "")[: self.MAX_OUTPUT], "stderr": str(exc.stderr or "")[: self.MAX_OUTPUT],
                    "duration_ms": round((time.perf_counter() - started) * 1000)}
        except (OSError, ValueError) as exc:
            return {"status": "error", "risk": risk.value, "reason": str(exc)[:300], "command": command, "args": args}
