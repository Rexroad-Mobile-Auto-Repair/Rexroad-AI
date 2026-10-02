import json
import re
import sqlite3
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class ManagedWorktrees:
    """Create isolated copies of registered Git roots without copying dirty files."""

    def __init__(self, workspaces, database_path, directory):
        self.workspaces = workspaces
        self.database_path = Path(database_path)
        self.directory = Path(directory).resolve()
        self.lock = threading.Lock()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS managed_worktrees (id TEXT PRIMARY KEY, record_json TEXT NOT NULL)"
            )
        for record in self.list():
            if record["status"] == "ready" and self._valid(record):
                self.workspaces.register_managed(record["workspace"], Path(record["path"]))

    @staticmethod
    def _git(root, *args):
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
            check=False,
        )
        if result.returncode:
            raise ValueError(
                "Git could not complete this operation; existing project files were preserved"
            )
        return result.stdout.strip()

    def preview(self, workspace):
        root = self.workspaces.get_root(workspace)
        if workspace.startswith("isolated_"):
            raise ValueError("Select an original project to create an isolated workspace")
        top = Path(self._git(root, "rev-parse", "--show-toplevel")).resolve()
        if top != root:
            raise ValueError("Isolation requires a registered whole Git project")
        return {
            "source_workspace": workspace,
            "base_commit": self._git(root, "rev-parse", "HEAD"),
            "branch": self._git(root, "branch", "--show-current"),
            "has_unsaved_changes": bool(self._git(root, "status", "--porcelain")),
            "copies_unsaved_changes": False,
        }

    def _put(self, record):
        with sqlite3.connect(self.database_path) as db:
            db.execute(
                "INSERT OR REPLACE INTO managed_worktrees VALUES (?, ?)",
                (record["id"], json.dumps(record)),
            )

    def _valid(self, record):
        try:
            if (
                not re.fullmatch(r"[a-f0-9]{12}", record["id"])
                or record["workspace"] != "isolated_" + record["id"]
                or record["branch"] != "codex/isolated-" + record["id"]
            ):
                return False
            path = Path(record["path"])
            if path.name != record["id"]:
                return False
            if (
                path.parent.resolve() != self.directory
                or path.is_symlink()
                or path.resolve().parent != self.directory
            ):
                return False
            source = self.workspaces.get_root(record["source_workspace"])
            actual_common = Path(
                self._git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
            ).resolve()
            expected_common = Path(
                self._git(source, "rev-parse", "--path-format=absolute", "--git-common-dir")
            ).resolve()
            return (
                actual_common == expected_common
                and Path(self._git(path, "rev-parse", "--show-toplevel")).resolve()
                == path.resolve()
            )
        except (KeyError, OSError, ValueError, PermissionError, subprocess.TimeoutExpired):
            return False

    def list(self):
        with sqlite3.connect(self.database_path) as db:
            records = []
            for row in db.execute(
                "SELECT record_json FROM managed_worktrees ORDER BY rowid DESC LIMIT 20"
            ):
                try:
                    record = json.loads(row[0])
                    if isinstance(record, dict) and all(
                        key in record
                        for key in (
                            "id",
                            "workspace",
                            "source_workspace",
                            "path",
                            "branch",
                            "status",
                        )
                    ):
                        records.append(record)
                except (TypeError, ValueError):
                    continue
            return records

    def create(self, workspace, base_commit):
        with self.lock:
            preview = self.preview(workspace)
            if base_commit != preview["base_commit"]:
                raise ValueError("The saved commit changed; preview isolation again")
            if len(self.list()) >= 20:
                raise ValueError("The managed-workspace limit has been reached")
            self.directory.mkdir(parents=True, exist_ok=True)
            if self.directory.is_symlink():
                raise PermissionError("Managed-workspace directory is unavailable")
            identifier = uuid4().hex[:12]
            name = "isolated_" + identifier
            path = self.directory / identifier
            record = {
                **preview,
                "id": identifier,
                "workspace": name,
                "path": str(path),
                "branch": "codex/isolated-" + identifier,
                "status": "creating",
                "created_at": datetime.now(UTC).isoformat(),
            }
            self._put(record)
            try:
                source = self.workspaces.get_root(workspace)
                self._git(
                    source,
                    "-c",
                    "core.hooksPath=" + str(self.directory / "disabled-hooks"),
                    "worktree",
                    "add",
                    "-b",
                    record["branch"],
                    str(path),
                    base_commit,
                )
                if not self._valid(record):
                    raise ValueError("The isolated workspace could not be verified")
                self.workspaces.register_managed(name, path)
                record["status"] = "ready"
                self._put(record)
                return record
            except (ValueError, OSError, subprocess.TimeoutExpired):
                record["status"] = "failed"
                self._put(record)
                raise ValueError(
                    "Isolation did not complete; the original project was preserved and the attempt was saved"
                ) from None
