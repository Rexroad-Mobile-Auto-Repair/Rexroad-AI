from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.knowledge.models import KnowledgeChunk


class KnowledgeStore:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    chunk_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS knowledge_chunks_workspace
                ON knowledge_chunks (workspace, file_path)
                """
            )

    def replace_workspace(
        self,
        workspace: str,
        chunks: list[KnowledgeChunk],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM knowledge_chunks WHERE workspace = ?",
                (workspace,),
            )
            connection.executemany(
                """
                INSERT INTO knowledge_chunks (
                    chunk_id, workspace, file_path, chunk_json
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        chunk.chunk_id,
                        chunk.workspace,
                        chunk.file_path,
                        chunk.model_dump_json(),
                    )
                    for chunk in chunks
                ],
            )

    def list_workspace(self, workspace: str) -> list[KnowledgeChunk]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT chunk_json FROM knowledge_chunks
                WHERE workspace = ?
                ORDER BY file_path, chunk_id
                """,
                (workspace,),
            ).fetchall()

        chunks = [
            KnowledgeChunk.model_validate(json.loads(row["chunk_json"]))
            for row in rows
        ]
        return sorted(
            chunks,
            key=lambda chunk: (
                chunk.file_path,
                chunk.line_start,
                chunk.line_end,
                chunk.chunk_id,
            ),
        )
