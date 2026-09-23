from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.memory.models import MemoryRecord


class MemoryStore:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS memory_records (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, category TEXT NOT NULL,
                    content TEXT NOT NULL, status TEXT NOT NULL, provenance TEXT NOT NULL,
                    source_reference TEXT, supersedes TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory_records(scope, status, updated_at)")

    def put(self, record: MemoryRecord) -> MemoryRecord:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("""
                INSERT OR REPLACE INTO memory_records
                (id, scope, category, content, status, provenance, source_reference, supersedes, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (record.id, record.scope, record.category, record.content, record.status, record.provenance,
                   record.source_reference, record.supersedes, record.created_at.isoformat(), record.updated_at.isoformat(),
                   json.dumps(record.metadata, ensure_ascii=False, sort_keys=True)))
        return record

    def get(self, record_id: str) -> MemoryRecord | None:
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM memory_records WHERE id = ?", (record_id,)).fetchone()
        return self._row(row) if row else None

    def delete(self, record_id: str) -> bool:
        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute("DELETE FROM memory_records WHERE id = ?", (record_id,))
            return cursor.rowcount == 1

    def list(self, scope: str, category: str | None = None, status: str | None = None, limit: int = 50) -> list[MemoryRecord]:
        clauses = ["scope = ?"]; values: list[object] = [scope]
        if category: clauses.append("category = ?"); values.append(category)
        if status: clauses.append("status = ?"); values.append(status)
        values.append(limit)
        query = f"SELECT * FROM memory_records WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC, id LIMIT ?"
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, values).fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(id=row["id"], scope=row["scope"], category=row["category"], content=row["content"],
                            status=row["status"], provenance=row["provenance"], source_reference=row["source_reference"],
                            supersedes=row["supersedes"], created_at=row["created_at"], updated_at=row["updated_at"],
                            metadata=json.loads(row["metadata_json"]))
