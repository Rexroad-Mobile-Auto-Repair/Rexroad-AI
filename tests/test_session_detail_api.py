
from fastapi.testclient import TestClient

import app.main as main_module
from app.journal.models import ActionEntry, SessionSummary


class FakeJournal:
    def get_session(
        self,
        session_id: str,
    ) -> SessionSummary | None:
        if session_id == "missing":
            return None

        from datetime import UTC, datetime

        now = datetime.now(UTC)

        return SessionSummary(
            session_id=session_id,
            provider="openai_compatible",
            model="qwen",
            action_count=1,
            success_count=1,
            error_count=0,
            started_at=now,
            last_action_at=now,
        )

    def list_session(
        self,
        session_id: str,
    ) -> list[ActionEntry]:
        from datetime import UTC, datetime

        return [
            ActionEntry(
                id="action-1",
                session_id=session_id,
                provider="openai_compatible",
                model="qwen",
                tool="git.status",
                permission="read",
                arguments={"workspace": "seo_crawler"},
                status="success",
                result_preview="## main",
                created_at=datetime.now(UTC),
            )
        ]


def test_session_detail(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "action_journal",
        FakeJournal(),
    )

    client = TestClient(main_module.app)

    response = client.get("/sessions/session-123")

    assert response.status_code == 200

    body = response.json()

    assert body["summary"]["session_id"] == "session-123"
    assert body["summary"]["action_count"] == 1
    assert len(body["actions"]) == 1
    assert body["actions"][0]["tool"] == "git.status"


def test_session_detail_not_found(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "action_journal",
        FakeJournal(),
    )

    client = TestClient(main_module.app)

    response = client.get("/sessions/missing")

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Session not found"
    }
