from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.journal.models import (
    ActionEntry,
    ActionStatus,
    AgentEvent,
    EventType,
    SessionSummary,
)


class ActionJournal:
    EVENT_CONTENT_LIMIT = 2000

    @staticmethod
    def _session_title(connection: sqlite3.Connection, session_id: str) -> str:
        row = connection.execute("SELECT payload_json FROM agent_events WHERE session_id = ? AND event_type = 'user_request' ORDER BY sequence LIMIT 1", (session_id,)).fetchone()
        if row is None:
            return "New conversation"
        try:
            message = str(json.loads(row[0]).get("content", "")).strip()
        except (TypeError, ValueError):
            return "New conversation"
        return (message[:60].rstrip() + ("…" if len(message) > 60 else "")) or "New conversation"
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
                CREATE TABLE IF NOT EXISTS agent_events (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    action_id TEXT,
                    tool_call_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (session_id, sequence)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_events_session
                ON agent_events (session_id, sequence)
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

    def append_event(
        self,
        *,
        session_id: str,
        event_type: EventType,
        action_id: str | None = None,
        tool_call_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AgentEvent:
        event = AgentEvent(
            id=str(uuid4()),
            session_id=session_id,
            sequence=0,
            event_type=event_type,
            action_id=action_id,
            tool_call_id=tool_call_id,
            payload=self._bounded_payload(payload or {}),
            created_at=datetime.now(UTC),
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM agent_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            sequence = int(row["next_sequence"])
            event.sequence = sequence
            connection.execute(
                """
                INSERT INTO agent_events
                    (id, session_id, sequence, event_type, action_id, tool_call_id, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event.id, event.session_id, event.sequence, event.event_type, event.action_id,
                 event.tool_call_id, json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
                 event.created_at.isoformat()),
            )
        return event

    def list_events_for_session(self, session_id: str) -> list[AgentEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id, sequence, event_type, action_id, tool_call_id, payload_json, created_at
                FROM agent_events WHERE session_id = ? ORDER BY sequence
                """, (session_id,),
            ).fetchall()
        return [
            AgentEvent(
                id=row["id"], session_id=row["session_id"], sequence=row["sequence"],
                event_type=row["event_type"], action_id=row["action_id"],
                tool_call_id=row["tool_call_id"], payload=json.loads(row["payload_json"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    @classmethod
    def _bounded_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, str):
                safe[key] = value[: cls.EVENT_CONTENT_LIMIT]
            elif isinstance(value, (int, float, bool)) or value is None:
                safe[key] = value
            else:
                safe[key] = str(value)[: cls.EVENT_CONTENT_LIMIT]
        return safe

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

    def list_sessions(
        self,
        limit: int = 50,
    ) -> list[SessionSummary]:
        if limit < 1 or limit > 500:
            raise ValueError(
                "limit must be between 1 and 500"
            )

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.session_id,
                    MIN(a.provider) AS provider,
                    MIN(a.model) AS model,
                    COUNT(DISTINCT a.id) AS action_count,
                    COUNT(DISTINCT CASE WHEN a.status = 'success' THEN a.id END) AS success_count,
                    COUNT(DISTINCT CASE WHEN a.status = 'error' THEN a.id END) AS error_count,
                    MIN(a.created_at) AS started_at,
                    MAX(a.created_at) AS last_action_at,
                    MIN(e.created_at) AS event_started_at,
                    MAX(e.created_at) AS event_last_at
                FROM (SELECT session_id FROM action_journal UNION SELECT session_id FROM agent_events) s
                LEFT JOIN action_journal a ON a.session_id = s.session_id
                LEFT JOIN agent_events e ON e.session_id = s.session_id
                GROUP BY s.session_id
                ORDER BY COALESCE(last_action_at, event_last_at) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        with self._connect() as connection:
            titles = {row["session_id"]: self._session_title(connection, row["session_id"]) for row in rows}
        return [
            SessionSummary(
                session_id=row["session_id"],
                title=titles[row["session_id"]],
                provider=row["provider"] or "unknown",
                model=row["model"] or "unknown",
                action_count=row["action_count"],
                success_count=row["success_count"],
                error_count=row["error_count"],
                started_at=datetime.fromisoformat(row["started_at"] or row["event_started_at"]),
                last_action_at=datetime.fromisoformat(row["last_action_at"] or row["event_last_at"]),
            )
            for row in rows
        ]

    def get_session(
        self,
        session_id: str,
    ) -> SessionSummary | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.session_id,
                    MIN(a.provider) AS provider,
                    MIN(a.model) AS model,
                    COUNT(DISTINCT a.id) AS action_count,
                    COUNT(DISTINCT CASE WHEN a.status = 'success' THEN a.id END) AS success_count,
                    COUNT(DISTINCT CASE WHEN a.status = 'error' THEN a.id END) AS error_count,
                    MIN(a.created_at) AS started_at,
                    MAX(a.created_at) AS last_action_at,
                    MIN(e.created_at) AS event_started_at,
                    MAX(e.created_at) AS event_last_at
                FROM (SELECT session_id FROM action_journal UNION SELECT session_id FROM agent_events) s
                LEFT JOIN action_journal a ON a.session_id = s.session_id
                LEFT JOIN agent_events e ON e.session_id = s.session_id
                WHERE s.session_id = ?
                GROUP BY s.session_id
                """,
                (session_id,),
            ).fetchone()

        if row is None:
            return None

        with self._connect() as connection:
            title = self._session_title(connection, session_id)
        return SessionSummary(
            session_id=row["session_id"],
            title=title,
            provider=row["provider"] or "unknown",
            model=row["model"] or "unknown",
            action_count=row["action_count"],
            success_count=row["success_count"],
            error_count=row["error_count"],
            started_at=datetime.fromisoformat(row["started_at"] or row["event_started_at"]),
            last_action_at=datetime.fromisoformat(row["last_action_at"] or row["event_last_at"]),
        )
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

