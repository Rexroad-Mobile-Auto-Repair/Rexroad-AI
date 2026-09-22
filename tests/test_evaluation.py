from copy import deepcopy

from app.evaluation.models import EvaluationSuiteResult, RetrievalBenchmarkCase, RetrievalCaseResult
from app.evaluation.runner import EvaluationRunner
from app.knowledge.models import Evidence, KnowledgeChunk, KnowledgeSearchResult


def result(identifier: str, file_path: str = "app/example.py", rank: int = 1, freshness: str = "current"):
    chunk = KnowledgeChunk(
        chunk_id=identifier, content=identifier, workspace="repo", file_path=file_path,
        language="python", line_start=1, line_end=2, symbol_name="target",
        symbol_type="function", content_hash="hash", git_commit_sha="sha",
        indexed_at="2026-01-01T00:00:00Z",
    )
    return KnowledgeSearchResult(
        chunk=chunk,
        evidence=Evidence(
            chunk_id=identifier, workspace="repo", file_path=file_path, line_start=1,
            line_end=2, symbol_name="target", symbol_type="function", content_hash="hash",
            git_commit_sha="sha", retrieval_method="lexical", score=1, rank=rank,
            freshness=freshness,
        ),
    )


class FakeKnowledge:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def search(self, workspace, query, limit, mode):
        self.calls.append((workspace, query, limit, mode))
        return self.values


class SequenceKnowledge(FakeKnowledge):
    def __init__(self, sequences):
        super().__init__(sequences[0])
        self.sequences = sequences

    def search(self, workspace, query, limit, mode):
        self.calls.append((workspace, query, limit, mode))
        return self.sequences[min(len(self.calls) - 1, len(self.sequences) - 1)]


def case(**kwargs):
    values = {
        "case_id": "case", "workspace": "repo", "query": "target", "top_k": 3,
        "expected_file_path": "app/example.py",
    }
    values.update(kwargs)
    return RetrievalBenchmarkCase(**values)


def test_passing_case_repeatability_and_provenance():
    knowledge = FakeKnowledge([result("one")])
    before = deepcopy(knowledge.values)
    outcome = EvaluationRunner(knowledge).run_case(case(expected_symbol_name="target", expected_freshness="current"))
    assert outcome.status == "passed"
    assert outcome.observed_rank == 1
    assert outcome.repeatable is True
    assert outcome.ordered_result_ids == ["one"]
    assert len(knowledge.calls) == 2
    assert knowledge.values == before


def test_stable_id_case_passes():
    outcome = EvaluationRunner(FakeKnowledge([result("stable")])).run_case(
        RetrievalBenchmarkCase(case_id="id", workspace="repo", query="q", top_k=1, expected_evidence_id="stable")
    )
    assert outcome.status == "passed"


def test_exact_file_case_and_freshness_pass():
    outcome = EvaluationRunner(FakeKnowledge([result("one")])).run_case(case(expected_freshness="current"))
    assert outcome.status == "passed"


def test_matching_failures_and_rank_boundaries():
    knowledge = FakeKnowledge([result("one", rank=1), result("two", "other.py", rank=2)])
    runner = EvaluationRunner(knowledge)
    assert runner.run_case(case(max_rank=1)).status == "passed"
    exceeded = runner.run_case(case(expected_file_path="other.py", max_rank=1))
    assert exceeded.status == "failed"
    assert exceeded.reason == "rank_exceeded"
    missing = runner.run_case(case(expected_file_path="missing.py"))
    assert missing.reason == "expected_result_missing"
    mismatch = runner.run_case(case(expected_symbol_name="wrong"))
    assert mismatch.reason == "symbol_mismatch"
    freshness = EvaluationRunner(FakeKnowledge([result("one", freshness="stale")])).run_case(
        case(expected_freshness="current")
    )
    assert freshness.reason == "freshness_mismatch"


def test_suite_summary_and_pass_rate():
    knowledge = FakeKnowledge([result("one")])
    suite = EvaluationRunner(knowledge).run_suite("suite", [case(), case(expected_file_path="missing.py")])
    assert suite.total == 2
    assert suite.passed == 1
    assert suite.failed == 1
    assert suite.pass_rate == 0.5


def test_suite_status_denominator_and_all_skipped_behavior():
    results = [
        RetrievalCaseResult(case_id="pass", status="passed"),
        RetrievalCaseResult(case_id="error", status="error"),
        RetrievalCaseResult(case_id="skip", status="skipped"),
    ]
    suite = EvaluationSuiteResult(
        suite_id="manual", cases=results, total=3, passed=1, failed=0,
        skipped=1, errors=1, pass_rate=1 / 2,
    )
    assert suite.pass_rate == 0.5
    all_skipped = EvaluationSuiteResult(
        suite_id="skipped", cases=[], total=0, passed=0, failed=0,
        skipped=0, errors=0, pass_rate=0.0,
    )
    assert all_skipped.pass_rate == 0.0


def test_safe_retrieval_error():
    class Broken:
        def search(self, *args):
            raise RuntimeError("secret-token")

    outcome = EvaluationRunner(Broken()).run_case(case())
    assert outcome.status == "error"
    assert outcome.reason == "retrieval_error"
    assert "secret-token" not in outcome.model_dump_json()


def test_repeatability_detects_order_line_freshness_and_method_changes():
    first = [result("one"), result("two", "other.py", rank=2)]
    second = [result("two", "other.py", rank=2), result("one")]
    outcome = EvaluationRunner(SequenceKnowledge([first, second])).run_case(case())
    assert outcome.status == "failed"
    assert outcome.reason == "repeatability_mismatch"

    changed_line = result("one")
    changed_line.evidence.line_end = 99
    outcome = EvaluationRunner(SequenceKnowledge([[result("one")], [changed_line]])).run_case(case())
    assert outcome.reason == "repeatability_mismatch"

    changed_freshness = result("one", freshness="stale")
    outcome = EvaluationRunner(SequenceKnowledge([[result("one")], [changed_freshness]])).run_case(case())
    assert outcome.reason == "repeatability_mismatch"

    changed_method = result("one")
    changed_method.evidence.retrieval_method = "semantic"
    outcome = EvaluationRunner(SequenceKnowledge([[result("one")], [changed_method]])).run_case(case())
    assert outcome.reason == "repeatability_mismatch"


def test_score_only_difference_is_repeatable():
    first = result("one")
    second = result("one")
    second.evidence.score = 999
    outcome = EvaluationRunner(SequenceKnowledge([[first], [second]])).run_case(case())
    assert outcome.status == "passed"
    assert outcome.repeatable is True


def test_unsafe_observed_path_is_safe():
    unsafe = result("one", file_path=r"C:\secret\file.py")
    outcome = EvaluationRunner(FakeKnowledge([unsafe])).run_case(case())
    assert outcome.status == "error"
    assert outcome.reason == "unsafe_result_path"
    assert "C:\\secret" not in outcome.model_dump_json()


def test_case_validation_rejects_ambiguous_or_unsafe_inputs():
    import pytest

    with pytest.raises(ValueError):
        RetrievalBenchmarkCase(case_id="", workspace="repo", query="q", top_k=1, expected_file_path="x.py")
    with pytest.raises(ValueError):
        RetrievalBenchmarkCase(case_id="x", workspace="repo", query="q", top_k=0, expected_file_path="x.py")
    with pytest.raises(ValueError):
        RetrievalBenchmarkCase(case_id="x", workspace="repo", query="q", top_k=1, max_rank=2, expected_file_path="x.py")
    with pytest.raises(ValueError):
        RetrievalBenchmarkCase(case_id="x", workspace="repo", query="q", top_k=1)
    with pytest.raises(ValueError):
        RetrievalBenchmarkCase(case_id="x", workspace="repo", query="q", top_k=1, expected_file_path="C:/repo/x.py")
    with pytest.raises(ValueError):
        RetrievalBenchmarkCase(case_id="x", workspace="repo", query="q", top_k=1, expected_evidence_id=" ")
    with pytest.raises(ValueError):
        RetrievalBenchmarkCase(case_id="x", workspace="repo", query="q", top_k=1, expected_file_path="../x.py")
    with pytest.raises(ValueError):
        RetrievalBenchmarkCase(case_id="x", workspace="repo", query="q", top_k=1, expected_file_path="app/../../x.py")
    with pytest.raises(ValueError):
        RetrievalBenchmarkCase(case_id="x", workspace="repo", query="q", top_k=1, expected_file_path="x.py", expected_evidence_id="id")
    with pytest.raises(ValueError):
        RetrievalBenchmarkCase(case_id="x", workspace="repo", query="q", top_k=1, expected_file_path="x.py", expected_symbol_name=" ")
