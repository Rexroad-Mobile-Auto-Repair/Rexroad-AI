"""Bounded Python syntax navigation; candidates are not resolved symbol bindings.

Clawd's LSP navigation interface was reviewed as a reference. This implementation
uses Python's parser and Rexroad's existing confined search, without an LSP server.
"""

import ast
import json

from app.tools.code_search import (
    MAX_FILE_BYTES,
    MAX_OUTPUT_CHARS,
    MAX_READ_BYTES,
    WorkspaceCodeSearch,
)


class PythonSymbols:
    def __init__(self, search: WorkspaceCodeSearch) -> None:
        self.search = search

    def find(
        self,
        workspace: str,
        symbol: str,
        kind: str = "definitions",
        pattern: str = "*.py",
        limit: int = 20,
    ) -> dict:
        if not isinstance(symbol, str) or not symbol.isidentifier() or len(symbol) > 100:
            raise ValueError("Enter one Python function, class, or variable name")
        if kind not in {"definitions", "references"}:
            raise ValueError("Choose definitions or references")
        root, base = self.search._validate(workspace, ".", pattern, limit)
        stats = {"scan_truncated": False, "scanned_files": 0, "skipped_files": 0}
        matches, read_bytes, output_chars, truncated = [], 0, 0, False
        for path in self.search._files(workspace, root, base, pattern, stats):
            if path.suffix.casefold() != ".py":
                continue
            try:
                with path.open("rb") as source:
                    data = source.read(MAX_FILE_BYTES + 1)
                read_bytes += len(data)
                if read_bytes > MAX_READ_BYTES:
                    stats["scan_truncated"] = True
                    break
                if len(data) > MAX_FILE_BYTES or b"\x00" in data:
                    stats["skipped_files"] += 1
                    continue
                content = data.decode("utf-8")
                tree = ast.parse(content)
            except (OSError, UnicodeError, SyntaxError, RecursionError):
                stats["skipped_files"] += 1
                continue
            stats["scanned_files"] += 1
            lines = content.splitlines()
            nodes = []
            for node in ast.walk(tree):
                if kind == "definitions":
                    found = (
                        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                        and node.name == symbol
                    )
                else:
                    found = (
                        (
                            isinstance(node, ast.Name)
                            and isinstance(node.ctx, ast.Load)
                            and node.id == symbol
                        )
                        or (
                            isinstance(node, ast.Attribute)
                            and isinstance(node.ctx, ast.Load)
                            and node.attr == symbol
                        )
                        or (
                            isinstance(node, ast.alias)
                            and (node.name.split(".")[-1] == symbol or node.asname == symbol)
                        )
                    )
                if found:
                    nodes.append(node)
            for node in sorted(nodes, key=lambda n: (n.lineno, n.col_offset)):
                item = {
                    "file": path.relative_to(root).as_posix(),
                    "line": node.lineno,
                    "column": node.col_offset + 1,
                    "text": lines[node.lineno - 1][:300],
                    "context": [],
                    "kind": "class"
                    if isinstance(node, ast.ClassDef)
                    else "function"
                    if kind == "definitions"
                    else "possible_reference",
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
            "symbol": symbol,
            "mode": kind,
            "language": "python",
            "binding_resolved": False,
            "matches": matches,
            "truncated": truncated or stats["scan_truncated"],
            **stats,
        }
