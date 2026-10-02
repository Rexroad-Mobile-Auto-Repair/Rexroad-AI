from pathlib import Path

from pydantic import BaseModel


class WorkspaceAccessError(PermissionError):
    pass


class WorkspaceInfo(BaseModel):
    name: str
    available: bool


class WorkspaceRegistry:
    def __init__(self, workspaces: dict[str, Path]) -> None:
        self._workspaces = {
            name: path.resolve()
            for name, path in workspaces.items()
        }

    def names(self) -> list[str]:
        return sorted(self._workspaces)

    def register_managed(self, name: str, root: Path) -> None:
        """Called only by the managed-worktree service, never from arbitrary paths."""
        root = root.resolve()
        if name in self._workspaces and self._workspaces[name] != root:
            raise WorkspaceAccessError("Workspace name is already registered")
        if not name.startswith("isolated_") or not root.is_dir():
            raise WorkspaceAccessError("Invalid managed workspace")
        self._workspaces[name] = root

    def list(self) -> list[WorkspaceInfo]:
        return [self.inspect(name) for name in self.names()]

    def inspect(self, name: str) -> WorkspaceInfo:
        root = self.get_root(name)
        return WorkspaceInfo(name=name, available=root.is_dir())

    def get_root(self, name: str) -> Path:
        try:
            return self._workspaces[name]
        except KeyError as exc:
            raise WorkspaceAccessError(
                f"Unknown workspace: {name}"
            ) from exc

    def resolve_path(
        self,
        workspace: str,
        relative_path: str = ".",
    ) -> Path:
        root = self.get_root(workspace)
        candidate = (root / relative_path).resolve()

        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise WorkspaceAccessError(
                "Path escapes approved workspace"
            ) from exc

        return candidate
