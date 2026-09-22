from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.journal.models import ActionEntry, ActionStatus


class ActionJournal:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS action_journal (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_preview TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_action_journal_session_id
                ON action_journal (session_id)
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_action_journal_created_at
                ON action_journal (created_at)
                """
            )

    def record(
        self,
        *,
        session_id: str,
        provider: str,
        model: str,
        tool: str,
        permission: str,
        arguments: dict,
        status: ActionStatus,
        result_preview: str | None = None,
        error: str | None = None,
    ) -> ActionEntry:
        entry = ActionEntry(
            id=str(uuid4()),
            session_id=session_id,
            provider=provider,
            model=model,
            tool=tool,
            permission=permission,
            arguments=arguments,
            status=status,
            result_preview=result_preview,
            error=error,
            created_at=datetime.now(UTC),
        )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO action_journal (
                    id,
                    session_id,
                    provider,
                    model,
                    tool,
                    permission,
                    arguments_json,
                    status,
                    result_preview,
                    error,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.session_id,
                    entry.provider,
                    entry.model,
                    entry.tool,
                    entry.permission,
                    json.dumps(
                        entry.arguments,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    entry.status,
                    entry.result_preview,
                    entry.error,
                    entry.created_at.isoformat(),
                ),
            )

        return entry

    def list_session(
        self,
        session_id: str,
    ) -> list[ActionEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    session_id,
                    provider,
                    model,
                    tool,
                    permission,
                    arguments_json,
                    status,
                    result_preview,
                    error,
                    created_at
                FROM action_journal
                WHERE session_id = ?
                ORDER BY created_at, id
                """,
                (session_id,),
            ).fetchall()

        return [
            self._row_to_entry(row)
            for row in rows
        ]

    def list_recent(
        self,
        limit: int = 50,
    ) -> list[ActionEntry]:
        if limit < 1 or limit > 500:
            raise ValueError(
                "limit must be between 1 and 500"
            )

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    session_id,
                    provider,
                    model,
                    tool,
                    permission,
                    arguments_json,
                    status,
                    result_preview,
                    error,
                    created_at
                FROM action_journal
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            self._row_to_entry(row)
            for row in rows
        ]

    @staticmethod
    def _row_to_entry(
        row: sqlite3.Row,
    ) -> ActionEntry:
        return ActionEntry(
            id=row["id"],
            session_id=row["session_id"],
            provider=row["provider"],
            model=row["model"],
            tool=row["tool"],
            permission=row["permission"],
            arguments=json.loads(
                row["arguments_json"]
            ),
            status=row["status"],
            result_preview=row["result_preview"],
            error=row["error"],
            created_at=datetime.fromisoformat(
                row["created_at"]
            ),
        )
