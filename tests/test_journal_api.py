from datetime import UTC, datetime

from fastapi.testclient import TestClient

import app.main as main_module
from app.journal.models import ActionEntry


class FakeJournal:
    def list_session(
        self,
        session_id: str,
    ) -> list[ActionEntry]:
        return [
            ActionEntry(
                id="action-1",
                session_id=session_id,
                provider="openai_compatible",
                model="qwen3-coder-30b-a3b-instruct",
                tool="git.status",
                permission="read",
                arguments={
                    "workspace": "seo_crawler",
                },
                status="success",
                result_preview="## main",
                created_at=datetime(
                    2026,
                    9,
                    22,
                    tzinfo=UTC,
                ),
            )
        ]


def test_journal_session_endpoint(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "action_journal",
        FakeJournal(),
    )

    client = TestClient(main_module.app)

    response = client.get(
        "/journal/session/session-123"
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data) == 1
    assert data[0]["session_id"] == "session-123"
    assert data[0]["tool"] == "git.status"
    assert data[0]["status"] == "success"
