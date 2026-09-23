from pathlib import Path

from app.journal.store import ActionJournal
from app.plans.traces import ExecutionTraceService


def test_trace_lookup_is_scoped_and_sanitized(tmp_path: Path):
    journal = ActionJournal(tmp_path / "journal.sqlite3")
    entry = journal.record(session_id="session", provider="plan", model="coordinator", tool="read", permission="read", arguments={"scope": "a", "plan_id": "p", "step_id": "s", "trace_id": "t", "verification_status": "passed", "verification_reason": "result_present"}, status="success", result_preview="{'token': '[redacted]', 'id': 'keep'}")
    service = ExecutionTraceService(journal)
    trace = service.get("t", "a")
    assert trace is not None
    assert trace.plan_id == "p"
    assert "[redacted]" in trace.result_preview
    assert service.get("t", "b") is None
    assert service.get("missing", "a") is None
    assert journal.list_session("session")[0].id == entry.id
