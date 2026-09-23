import json
from pathlib import Path

from app.config import Settings
from app.evaluation.evidence_ranking_benchmarks import (
    EVIDENCE_RANKING_BENCHMARK_SUITE_ID,
    EVIDENCE_RANKING_BENCHMARKS,
)


def test_benchmark_ids_and_targets_are_unique_and_deterministic():
    assert len({case.case_id for case in EVIDENCE_RANKING_BENCHMARKS}) == len(EVIDENCE_RANKING_BENCHMARKS)
    assert len({(case.target_file_path, case.target_symbol_name) for case in EVIDENCE_RANKING_BENCHMARKS}) == len(EVIDENCE_RANKING_BENCHMARKS)
    assert [case.case_id for case in EVIDENCE_RANKING_BENCHMARKS] == ["exact-analysis-runs", "indexability-concept"]


def test_benchmark_serialization_is_stable_and_limits_are_valid():
    first = json.dumps([case.model_dump(mode="json") for case in EVIDENCE_RANKING_BENCHMARKS], sort_keys=True, separators=(",", ":"))
    second = json.dumps([case.model_dump(mode="json") for case in EVIDENCE_RANKING_BENCHMARKS], sort_keys=True, separators=(",", ":"))
    assert first == second
    assert EVIDENCE_RANKING_BENCHMARK_SUITE_ID.endswith("-v1")
    assert all(1 <= case.retrieval_limit <= 50 and 1 <= case.selection_limit <= 50 for case in EVIDENCE_RANKING_BENCHMARKS)


def test_documented_targets_exist_in_configured_crawler_workspace():
    root = Path(Settings().seo_crawler_workspace)
    for case in EVIDENCE_RANKING_BENCHMARKS:
        assert (root / case.target_file_path).is_file()
