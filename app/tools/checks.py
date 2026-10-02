from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from app.policy.workspaces import WorkspaceRegistry
from app.tools.output_policy import sanitize_output


@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    args: tuple[str, ...]
    timeout_seconds: int
    allow_targets: bool = False


class WorkspaceChecks:
    MAX_OUTPUT = 8_000
    MAX_TARGETS = 20
    SPECS: ClassVar[dict[str, CheckSpec]] = {
        "pytest": CheckSpec("pytest", ("-m", "pytest", "-q"), 60, True),
        "ruff": CheckSpec("ruff", ("-m", "ruff", "check", "."), 30, False),
        "git_diff_check": CheckSpec("git_diff_check", (), 20, False),
    }

    def __init__(self, workspaces: WorkspaceRegistry) -> None:
        self._workspaces = workspaces

    def run_check(self, workspace: str, check_id: str, targets: list[str] | None = None) -> dict[str, Any]:
        root = self._workspaces.get_root(workspace)
        spec = self.SPECS.get(check_id)
        if spec is None:
            raise ValueError("unknown check")
        targets = targets or []
        if len(targets) > self.MAX_TARGETS or (targets and not spec.allow_targets):
            raise ValueError("invalid check targets")
        validated: list[str] = []
        for target in targets:
            if not target or any(marker in target for marker in (";", "|", "&", "`", "$")) or Path(target).is_absolute() or ".." in Path(target).parts:
                raise PermissionError("check target must remain workspace-relative")
            resolved = self._workspaces.resolve_path(workspace, target)
            if not resolved.is_file():
                raise FileNotFoundError("check target not found")
            validated.append(target)
        if check_id == "pytest":
            command = [sys.executable, *spec.args, *validated]
        elif check_id == "ruff":
            command = [sys.executable, *spec.args]
        else:
            command = ["git", "diff", "--check"]
        started = time.monotonic()
        env = {key: value for key, value in os.environ.items() if not any(secret in key.casefold() for secret in ("key", "token", "secret", "password", "credential"))}
        env["PYTHONUNBUFFERED"] = "1"
        if check_id == "pytest":
            temp_root = self._workspaces.resolve_path(workspace, ".pytest-tmp/checks")
            temp_root.mkdir(parents=True, exist_ok=True)
            env["PYTEST_DEBUG_TEMPROOT"] = str(temp_root)
        try:
            completed = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=spec.timeout_seconds, shell=False, env=env, check=False)
            timed_out = False
            stdout = completed.stdout
            stderr = completed.stderr
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = str(exc.stdout or "")
            stderr = str(exc.stderr or "")
            exit_code = None
        except OSError:
            raise ValueError("check execution failed") from None
        duration_ms = int((time.monotonic() - started) * 1000)
        result = {
            "check_id": check_id,
            "workspace": workspace,
            "status": "timeout" if timed_out else ("passed" if exit_code == 0 else "failed"),
            "exit_code": exit_code,
            "passed": not timed_out and exit_code == 0,
            "stdout": stdout[: self.MAX_OUTPUT],
            "stderr": stderr[: self.MAX_OUTPUT],
            "duration_ms": duration_ms,
            "timed_out": timed_out,
            "targets": validated,
        }
        return sanitize_output(result)
