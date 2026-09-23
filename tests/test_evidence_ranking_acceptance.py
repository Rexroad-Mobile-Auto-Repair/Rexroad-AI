import pytest

from app.evaluation.evidence_ranking_acceptance import (
    EvidenceRankingAcceptanceRunner,
    EvidenceRankingCase,
)
from app.knowledge.multi_query import MultiQueryResult
from tests.test_evidence_ranker import item, pool


class FakeRetriever:
    def __init__(self, result, second=None):
        self.result = result
        self.second = second or result

    async def search(self, workspace, query, limit, mode):
        value = self.result
        self.result, self.second = self.second, self.result
        return value


def case(symbol="target", path="target.py", limit=2):
    return EvidenceRankingCase(case_id="case", workspace="ws", query="question", target_file_path=path, target_symbol_name=symbol, selection_limit=limit)


def result_with_lanes(raw, planned, candidates=None):
    return MultiQueryResult(original_query="question", planning_status="planned", raw_results=[match.result for match in raw], planned_results=[match.result for match in planned], candidates=list(candidates if candidates is not None else [*raw, *planned]))


@pytest.mark.asyncio
async def test_target_is_reported_at_each_stage_and_selection_uses_ranked_order():
    target = item("target", raw_rank=2, path="target.py")
    pool_result = result_with_lanes([item("other", raw_rank=1, path="other.py"), target], [], [item("other", raw_rank=1, path="other.py"), target])
    output = await EvidenceRankingAcceptanceRunner(FakeRetriever(pool_result)).run_case(case())
    assert output.status == "observed"
    assert output.target_found and output.candidate_position == 2
    assert output.raw_position == 2 and output.planned_position is None
    assert output.ranked_position == 2 and output.selected_position == 2


@pytest.mark.asyncio
async def test_target_missing_is_safe():
    output = await EvidenceRankingAcceptanceRunner(FakeRetriever(pool(item("other", path="other.py")))).run_case(case())
    assert output.status == "target_missing"
    assert output.safe_reason == "target_not_in_candidate_pool"


@pytest.mark.asyncio
async def test_stale_target_is_ranked_but_not_selected():
    target = item("target", freshness="stale", raw_rank=1, path="target.py")
    output = await EvidenceRankingAcceptanceRunner(FakeRetriever(pool(target))).run_case(case(limit=1))
    assert output.status == "prerequisite_unavailable" and output.ranked_position == 1
    assert output.selected_position is None and output.safe_reason == "target_not_current"


@pytest.mark.asyncio
async def test_raw_only_target_reports_raw_position_and_found_by():
    target = item("target", path="target.py")
    output = await EvidenceRankingAcceptanceRunner(FakeRetriever(result_with_lanes([target], [], [target]))).run_case(case())
    assert output.raw_position == 1 and output.planned_position is None and output.found_by == "raw"


@pytest.mark.asyncio
async def test_planned_only_target_reports_planned_position_and_found_by():
    target = item("target", found_by="planned", raw_rank=None, planned_rank=1, path="target.py")
    output = await EvidenceRankingAcceptanceRunner(FakeRetriever(result_with_lanes([], [target], [target]))).run_case(case())
    assert output.raw_position is None and output.planned_position == 1 and output.found_by == "planned"


@pytest.mark.asyncio
async def test_both_lane_target_reports_both_positions_and_ranks():
    target = item("target", found_by="both", raw_rank=2, planned_rank=1, path="target.py")
    output = await EvidenceRankingAcceptanceRunner(FakeRetriever(result_with_lanes([target], [target], [target]))).run_case(case())
    assert output.raw_position == 1 and output.planned_position == 1
    assert output.found_by == "both" and output.raw_rank == 2 and output.planned_rank == 1


@pytest.mark.asyncio
async def test_missing_freshness_is_reported_and_not_selected():
    target = item("target", freshness="missing", path="target.py")
    output = await EvidenceRankingAcceptanceRunner(FakeRetriever(pool(target))).run_case(case(limit=1))
    assert output.status == "prerequisite_unavailable"
    assert output.freshness == "missing" and output.ranked_position == 1
    assert output.selected_position is None and output.safe_reason == "target_not_current"


@pytest.mark.asyncio
async def test_selection_limit_exclusion_is_explicit():
    target = item("target", raw_rank=3, path="target.py")
    candidates = pool(item("a", raw_rank=1), item("b", raw_rank=2), target)
    output = await EvidenceRankingAcceptanceRunner(FakeRetriever(candidates)).run_case(case(limit=2))
    assert output.ranked_position == 3 and output.selected_position is None
    assert output.safe_reason == "target_not_selected_limit"


class FailingRetriever:
    async def search(self, *args):
        raise RuntimeError("secret-token")


@pytest.mark.asyncio
async def test_retrieval_exception_is_sanitized():
    output = await EvidenceRankingAcceptanceRunner(FailingRetriever()).run_case(case())
    assert output.status == "error" and output.safe_reason == "retrieval_error"
    assert "secret-token" not in output.model_dump_json()


@pytest.mark.asyncio
async def test_repeatability_mismatch_is_reported():
    first = pool(item("target", path="target.py"))
    second = pool(item("other", path="other.py"))
    output = await EvidenceRankingAcceptanceRunner(FakeRetriever(first, second)).run_case(case())
    assert output.status == "error" and output.safe_reason == "repeatability_mismatch"


def test_case_rejects_absolute_target_path():
    with pytest.raises(ValueError):
        EvidenceRankingCase(case_id="x", workspace="ws", query="q", target_file_path="C:/secret.py")


@pytest.mark.parametrize("symbol", ["", "   ", "bad\nsymbol", "bad\rsymbol"])
def test_case_rejects_unsafe_target_symbol(symbol):
    with pytest.raises(ValueError):
        EvidenceRankingCase(case_id="x", workspace="ws", query="q", target_file_path="x.py", target_symbol_name=symbol)


@pytest.mark.asyncio
async def test_acceptance_does_not_mutate_original_pool():
    original = pool(item("target", raw_rank=2, path="target.py"), item("other", raw_rank=1))
    before = original.model_dump(mode="json")
    await EvidenceRankingAcceptanceRunner(FakeRetriever(original)).run_case(case())
    assert original.model_dump(mode="json") == before
