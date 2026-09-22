from pathlib import Path

from app.journal.store import ActionJournal


def test_list_sessions_groups_actions(
    tmp_path: Path,
) -> None:
    journal = ActionJournal(
        tmp_path / "journal.sqlite3"
    )

    journal.record(
        session_id="session-1",
        provider="openai_compatible",
        model="qwen",
        tool="git.status",
        permission="read",
        arguments={},
        status="success",
    )

    journal.record(
        session_id="session-1",
        provider="openai_compatible",
        model="qwen",
        tool="git.branch",
        permission="read",
        arguments={},
        status="success",
    )

    journal.record(
        session_id="session-2",
        provider="openai_compatible",
        model="qwen",
        tool="git.log",
        permission="read",
        arguments={},
        status="error",
        error="failed",
    )

    sessions = journal.list_sessions(limit=10)

    assert len(sessions) == 2

    by_id = {
        session.session_id: session
        for session in sessions
    }

    first = by_id["session-1"]
    second = by_id["session-2"]

    assert first.action_count == 2
    assert first.success_count == 2
    assert first.error_count == 0

    assert second.action_count == 1
    assert second.success_count == 0
    assert second.error_count == 1


def test_list_sessions_enforces_limit(
    tmp_path: Path,
) -> None:
    journal = ActionJournal(
        tmp_path / "journal.sqlite3"
    )

    for invalid in (0, 501):
        try:
            journal.list_sessions(limit=invalid)
        except ValueError as exc:
            assert "between 1 and 500" in str(exc)
        else:
            raise AssertionError(
                "Expected ValueError"
            )
