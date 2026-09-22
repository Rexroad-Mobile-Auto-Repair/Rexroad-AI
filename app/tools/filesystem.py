from __future__ import annotations

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
