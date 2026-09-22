from pathlib import Path

from app.journal.store import ActionJournal


def test_record_and_read_action(
    tmp_path: Path,
) -> None:
    journal = ActionJournal(
        tmp_path / "journal.sqlite3"
    )

    entry = journal.record(
        session_id="session-1",
        provider="openai_compatible",
        model="qwen3-coder-30b-a3b-instruct",
        tool="git.status",
        permission="read",
        arguments={
            "workspace": "seo_crawler",
        },
        status="success",
        result_preview="## main",
    )

    actions = journal.list_session("session-1")

    assert len(actions) == 1
    assert actions[0].id == entry.id
    assert actions[0].session_id == "session-1"
    assert actions[0].provider == "openai_compatible"
    assert actions[0].model == "qwen3-coder-30b-a3b-instruct"
    assert actions[0].tool == "git.status"
    assert actions[0].permission == "read"
    assert actions[0].arguments == {
        "workspace": "seo_crawler",
    }
    assert actions[0].status == "success"
    assert actions[0].result_preview == "## main"
    assert actions[0].error is None


def test_journal_persists_between_instances(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "journal.sqlite3"

    first = ActionJournal(database_path)

    first.record(
        session_id="session-1",
        provider="openai",
        model="test-model",
        tool="filesystem.read",
        permission="read",
        arguments={
            "workspace": "repo",
            "relative_path": "README.md",
        },
        status="success",
        result_preview="hello",
    )

    second = ActionJournal(database_path)

    actions = second.list_session("session-1")

    assert len(actions) == 1
    assert actions[0].tool == "filesystem.read"


def test_sessions_are_isolated(
    tmp_path: Path,
) -> None:
    journal = ActionJournal(
        tmp_path / "journal.sqlite3"
    )

    journal.record(
        session_id="session-1",
        provider="openai",
        model="model-a",
        tool="git.status",
        permission="read",
        arguments={},
        status="success",
    )

    journal.record(
        session_id="session-2",
        provider="anthropic",
        model="model-b",
        tool="git.log",
        permission="read",
        arguments={},
        status="error",
        error="failed",
    )

    first = journal.list_session("session-1")
    second = journal.list_session("session-2")

    assert len(first) == 1
    assert len(second) == 1

    assert first[0].tool == "git.status"
    assert second[0].tool == "git.log"
    assert second[0].status == "error"
    assert second[0].error == "failed"
