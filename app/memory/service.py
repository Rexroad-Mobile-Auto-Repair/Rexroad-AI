from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.memory.models import MemoryCreate, MemoryRecord, MemoryUpdate
from app.memory.store import MemoryStore


class MemoryService:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def create(self, request: MemoryCreate) -> MemoryRecord:
        now = datetime.now(UTC)
        return self._store.put(MemoryRecord(id=str(uuid4()), scope=request.scope, category=request.category,
            content=request.content, status="active", provenance=request.provenance,
            source_reference=request.source_reference, supersedes=request.supersedes,
            created_at=now, updated_at=now, metadata=request.metadata))

    def get(self, record_id: str) -> MemoryRecord | None:
        return self._store.get(record_id)

    def list(self, scope: str, category: str | None = None, status: str = "active", limit: int = 50) -> list[MemoryRecord]:
        if not scope.strip() or limit < 1 or limit > 100:
            raise ValueError("invalid memory scope or limit")
        return self._store.list(scope, category, status, limit)

    def search(self, scope: str, query: str, limit: int = 20) -> list[MemoryRecord]:
        terms = {term.casefold() for term in query.split() if term.strip()}
        return [record for record in self.list(scope, limit=100) if terms <= set(record.content.casefold().split())][:limit]

    def update(self, record_id: str, request: MemoryUpdate) -> MemoryRecord | None:
        record = self.get(record_id)
        if record is None: return None
        update = {key: value for key, value in request.model_dump(exclude_unset=True).items() if value is not None}
        update["updated_at"] = datetime.now(UTC)
        values = record.model_copy(update=update)
        return self._store.put(values)

    def delete(self, record_id: str) -> bool:
        return self._store.delete(record_id)

    @staticmethod
    def format_for_context(records: list[MemoryRecord]) -> str:
        return "[Project Memory]\n" + "\n".join(f"{item.category}: {item.content} (source: {item.provenance})" for item in records)
