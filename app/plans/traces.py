from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.journal.store import ActionJournal
from app.tools.output_policy import sanitize_output


class ExecutionTrace(BaseModel):
    trace_id: str
    plan_id: str
    step_id: str
    tool: str
    status: str
    verification_status: str | None
    verification_reason: str | None
    resulting_step_status: str | None
    session_id: str
    created_at: datetime
    result_preview: str | None


class ExecutionTraceService:
    def __init__(self, journal: ActionJournal) -> None:
        self._journal = journal

    def get(self, trace_id: str, scope: str) -> ExecutionTrace | None:
        if not trace_id.strip() or not scope.strip():
            return None
        for entry in self._journal.list_recent(500):
            args = entry.arguments
            if args.get("trace_id") != trace_id or args.get("scope") != scope:
                continue
            preview = sanitize_output(entry.result_preview) if entry.result_preview is not None else None
            return ExecutionTrace(
                trace_id=trace_id,
                plan_id=str(args.get("plan_id", "")),
                step_id=str(args.get("step_id", "")),
                tool=entry.tool,
                status=entry.status,
                verification_status=args.get("verification_status"),
                verification_reason=args.get("verification_reason"),
                resulting_step_status="completed" if entry.status == "success" else "failed",
                session_id=entry.session_id,
                created_at=entry.created_at,
                result_preview=preview,
            )
        return None

    def list(self, scope: str, limit: int = 20) -> list[ExecutionTrace]:
        if not scope.strip() or limit < 1 or limit > 100:
            raise ValueError("invalid trace scope or limit")
        results: list[ExecutionTrace] = []
        for entry in self._journal.list_recent(500):
            args = entry.arguments
            if args.get("scope") != scope or not args.get("trace_id"):
                continue
            trace = self.get(str(args["trace_id"]), scope)
            if trace is not None:
                results.append(trace)
            if len(results) >= limit:
                break
        return results
