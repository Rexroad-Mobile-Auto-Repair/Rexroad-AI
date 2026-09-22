from __future__ import annotations

import time
from pathlib import PurePosixPath

from app.evaluation.models import (
    EvaluationSuiteResult,
    RetrievalBenchmarkCase,
    RetrievalCaseResult,
)
from app.knowledge.models import KnowledgeSearchResult
from app.knowledge.service import KnowledgeService


class EvaluationRunner:
    def __init__(self, knowledge: KnowledgeService) -> None:
        self._knowledge = knowledge

    def run_case(self, case: RetrievalBenchmarkCase) -> RetrievalCaseResult:
        started = time.perf_counter()
        try:
            first = self._knowledge.search(
                case.workspace, case.query, case.top_k, case.mode
            )
            second = self._knowledge.search(
                case.workspace, case.query, case.top_k, case.mode
            )
        except Exception:  # noqa: BLE001 - evaluation boundary returns safe result
            return RetrievalCaseResult(
                case_id=case.case_id,
                status="error",
                expected_evidence_id=case.expected_evidence_id,
                expected_file_path=case.expected_file_path,
                reason="retrieval_error",
                duration_ms=self._duration(started),
            )

        if any(self._unsafe_path(item.evidence.file_path) for item in [*first, *second]):
            return RetrievalCaseResult(
                case_id=case.case_id,
                status="error",
                expected_evidence_id=case.expected_evidence_id,
                expected_file_path=case.expected_file_path,
                reason="unsafe_result_path",
                duration_ms=self._duration(started),
            )

        repeatable = self._observable(first) == self._observable(second)
        result = self._match(case, first)
        result.repeatable = repeatable
        result.duration_ms = self._duration(started)
        if not repeatable:
            result.status = "failed"
            result.reason = "repeatability_mismatch"
        return result

    def run_suite(
        self, suite_id: str, cases: list[RetrievalBenchmarkCase]
    ) -> EvaluationSuiteResult:
        results = [self.run_case(case) for case in cases]
        passed = sum(result.status == "passed" for result in results)
        failed = sum(result.status == "failed" for result in results)
        skipped = sum(result.status == "skipped" for result in results)
        errors = sum(result.status == "error" for result in results)
        denominator = passed + failed + errors
        return EvaluationSuiteResult(
            suite_id=suite_id,
            cases=results,
            total=len(results),
            passed=passed,
            failed=failed,
            skipped=skipped,
            errors=errors,
            pass_rate=passed / denominator if denominator else 0.0,
        )

    @staticmethod
    def _duration(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 3)

    @staticmethod
    def _unsafe_path(value: str) -> bool:
        normalized = value.replace("\\", "/")
        return (
            not normalized
            or normalized.startswith("/")
            or (len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/")
            or any(part == ".." for part in PurePosixPath(normalized).parts)
        )

    @staticmethod
    def _observable(results: list[KnowledgeSearchResult]) -> list[tuple]:
        return [
            (
                item.chunk.chunk_id,
                item.evidence.file_path,
                item.evidence.line_start,
                item.evidence.line_end,
                item.evidence.symbol_name,
                item.evidence.symbol_type,
                item.evidence.freshness,
                item.evidence.retrieval_method,
                item.evidence.rank,
            )
            for item in results
        ]

    @classmethod
    def _match(
        cls, case: RetrievalBenchmarkCase, results: list[KnowledgeSearchResult]
    ) -> RetrievalCaseResult:
        base = RetrievalCaseResult(
            case_id=case.case_id,
            status="failed",
            expected_evidence_id=case.expected_evidence_id,
            expected_file_path=case.expected_file_path,
            ordered_result_ids=[item.chunk.chunk_id for item in results],
        )
        for rank, item in enumerate(results, start=1):
            evidence = item.evidence
            if case.expected_evidence_id is not None and item.chunk.chunk_id != case.expected_evidence_id:
                continue
            if case.expected_file_path is not None and evidence.file_path != case.expected_file_path:
                continue
            if case.expected_symbol_name is not None and evidence.symbol_name != case.expected_symbol_name:
                base.reason = "symbol_mismatch"
                continue
            if case.expected_freshness is not None and evidence.freshness != case.expected_freshness:
                base.reason = "freshness_mismatch"
                continue
            base.matched_evidence_id = item.chunk.chunk_id
            base.matched_file_path = evidence.file_path
            base.observed_rank = rank
            base.retrieval_method = evidence.retrieval_method
            base.observed_freshness = evidence.freshness
            if case.max_rank is not None and rank > case.max_rank:
                base.reason = "rank_exceeded"
                return base
            base.status = "passed"
            base.reason = None
            return base
        if base.reason is None:
            base.reason = "expected_result_missing"
        return base
