from pathlib import Path


class WorkspaceAccessError(PermissionError):
    pass


class WorkspaceRegistry:
    def __init__(self, workspaces: dict[str, Path]) -> None:
        self._workspaces = {
            name: path.resolve()
            for name, path in workspaces.items()
        }

    def names(self) -> list[str]:
        return sorted(self._workspaces)

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
