from datetime import UTC, datetime

from app.context.builder import ContextBuilder
from app.context.models import ContextRequest
from app.subagents import SubAgentContribution
from app.tools.models import ModelMessage


def test_worker_context_is_separate_bounded_and_deterministic():
    contribution = SubAgentContribution(task_id="t1", worker_profile="researcher", scope="s", workspace="w", summary="analysis" * 5000, references=[], tool_usage=[], parent_session_id=None, plan_id=None, step_id=None, accepted_at=datetime.now(UTC))
    result = ContextBuilder().build(ContextRequest(messages=[ModelMessage(role="user", content="q")], total_byte_budget=20000, tool_result_byte_budget=1000, supplemental_worker_context=[contribution], supplemental_context_byte_budget=4000))
    assert len(result.messages) == 2
    assert "supplemental worker analysis" in result.messages[-1].content
    assert len(result.messages[-1].content.encode()) <= 4200


def test_no_worker_context_preserves_existing_shape():
    result = ContextBuilder().build(ContextRequest(messages=[ModelMessage(role="user", content="q")], total_byte_budget=1000, tool_result_byte_budget=1000))
    assert len(result.messages) == 1
