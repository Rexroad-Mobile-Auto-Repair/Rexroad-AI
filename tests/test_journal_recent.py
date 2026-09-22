from pathlib import Path

from app.journal.store import ActionJournal


def test_list_recent_returns_newest_first(
    tmp_path: Path,
) -> None:
    journal = ActionJournal(
        tmp_path / "journal.sqlite3"
    )

    first = journal.record(
        session_id="session-1",
        provider="openai",
        model="model-a",
        tool="git.status",
        permission="read",
        arguments={},
        status="success",
        result_preview="first",
    )

    second = journal.record(
        session_id="session-2",
        provider="anthropic",
        model="model-b",
        tool="git.log",
        permission="read",
        arguments={},
        status="success",
        result_preview="second",
    )

    recent = journal.list_recent(limit=2)

    assert len(recent) == 2
    assert recent[0].id == second.id
    assert recent[1].id == first.id


def test_list_recent_enforces_limit(
    tmp_path: Path,
) -> None:
    journal = ActionJournal(
        tmp_path / "journal.sqlite3"
    )

    for invalid in (0, 501):
        try:
            journal.list_recent(limit=invalid)
        except ValueError as exc:
            assert "between 1 and 500" in str(exc)
        else:
            raise AssertionError(
                "Expected ValueError"
            )
