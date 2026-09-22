import json

import pytest

from app.evaluation.acceptance import _observation, main
from app.evaluation.acceptance_models import AcceptanceObservationCase
from app.evaluation.models import RetrievalBenchmarkCase
from app.knowledge.models import Evidence, KnowledgeChunk, KnowledgeSearchResult


def result(identifier="a", file_path="app/example.py", symbol="target", freshness="current", rank=1, score=1):
    chunk = KnowledgeChunk(chunk_id=identifier, content=identifier, workspace="seo_crawler",
        file_path=file_path, language="python", line_start=1, line_end=2, symbol_name=symbol,
        symbol_type="function", content_hash="hash", indexed_at="2026-01-01T00:00:00Z")
    return KnowledgeSearchResult(chunk=chunk, evidence=Evidence(chunk_id=identifier,
        workspace="seo_crawler", file_path=file_path, line_start=1, line_end=2,
        symbol_name=symbol, symbol_type="function", content_hash="hash",
        retrieval_method="lexical", score=score, rank=rank, freshness=freshness))


class FakeKnowledge:
    def __init__(self, sequences):
        self.sequences = sequences
        self.calls = []

    def search(self, workspace, query, limit, mode):
        self.calls.append((workspace, query, limit, mode))
        return self.sequences[min(len(self.calls) - 1, len(self.sequences) - 1)]


def observation_case(**kwargs):
    values = {"case_id": "case", "workspace": "seo_crawler", "query": "q", "top_k": 10,
              "target_file_path": "app/example.py", "target_symbol_name": "target"}
    values.update(kwargs)
    return AcceptanceObservationCase(**values)


def test_observation_found_forwards_logical_workspace_and_ignores_score():
    knowledge = FakeKnowledge([[result(score=1)], [result(score=9)]])
    outcome = _observation(knowledge, observation_case())
    assert outcome.status == "observed"
    assert outcome.observed_rank == 1
    assert outcome.repeatable is True
    assert knowledge.calls == [("seo_crawler", "q", 10, "lexical")] * 2


def test_observation_miss_is_not_failure_and_mismatch_is_reported():
    miss = _observation(FakeKnowledge([[], []]), observation_case())
    assert miss.status == "observed"
    assert miss.observed_rank is None
    assert miss.safe_reason == "target_not_in_top_k"
    mismatch = _observation(FakeKnowledge([[result("a")], [result("b")]]), observation_case())
    assert mismatch.repeatable is False


@pytest.mark.parametrize("freshness", ["stale", "missing"])
def test_noncurrent_observation_is_prerequisite_state(freshness):
    outcome = _observation(FakeKnowledge([[result(freshness=freshness)], [result(freshness=freshness)]]), observation_case())
    assert outcome.status == "prerequisite_unavailable"
    assert outcome.freshness == freshness


def test_observation_safe_path_and_raw_exception_are_not_leaked():
    unsafe = result(file_path="D:/secret/repo.py")
    outcome = _observation(FakeKnowledge([[unsafe], [unsafe]]), observation_case())
    assert outcome.status == "error"
    assert outcome.safe_reason == "unsafe_result_path"
    class Broken(FakeKnowledge):
        def search(self, *args):
            raise RuntimeError("secret-token")
    broken = _observation(Broken([[]]), observation_case())
    assert "secret-token" not in broken.model_dump_json()


@pytest.mark.parametrize("path", ["", "../x.py", "/x.py", "C:/x.py", "\\\\server\\x.py", "x\ny.py"])
def test_observation_target_path_validation(path):
    with pytest.raises(ValueError):
        observation_case(target_file_path=path)


def test_assertion_case_reuses_existing_benchmark_contract():
    case = RetrievalBenchmarkCase(case_id="a", workspace="seo_crawler", query="q", top_k=10,
        expected_file_path="app/example.py", expected_symbol_name="target")
    assert case.expected_file_path == "app/example.py"


def test_json_output_is_safe_and_observation_miss_exit_is_success(monkeypatch, capsys):
    from app.evaluation import acceptance
    monkeypatch.setattr(acceptance, "run_acceptance", lambda settings, workspace: {
        "workspace": workspace, "status": "ok", "assertions": [{"status": "passed"}],
        "observations": [{"status": "observed", "safe_reason": "target_not_in_top_k"}],
    })
    assert main(["--json"]) == 0
    output = capsys.readouterr().out
    assert "D:/" not in output
    json.loads(output)


@pytest.mark.parametrize("observation_status", ["error"])
def test_observation_error_statuses_make_exit_nonzero(monkeypatch, observation_status):
    from app.evaluation import acceptance
    monkeypatch.setattr(acceptance, "run_acceptance", lambda settings, workspace: {
        "workspace": workspace, "status": "ok", "assertions": [{"status": "passed"}],
            "observations": [{"status": observation_status}],
    })
    assert main([]) == 1


@pytest.mark.parametrize("reason", ["workspace_unavailable", "index_unavailable", "index_empty"])
def test_top_level_prerequisite_reason_is_safe(monkeypatch, capsys, reason):
    from app.evaluation import acceptance
    monkeypatch.setattr(acceptance, "run_acceptance", lambda settings, workspace: {
        "workspace": workspace, "status": "prerequisite_unavailable", "safe_reason": reason,
        "assertions": [], "observations": [],
    })
    assert main([]) == 1
    output = capsys.readouterr().out
    assert reason in output
    assert "D:\\" not in output


@pytest.mark.parametrize("workspace", ["", "bad\nname", "C:/repo", "\\\\server\\repo"])
def test_cli_workspace_rejected_without_echoing_value(workspace, capsys):
    assert main(["--workspace", workspace]) == 1
    output = capsys.readouterr().out
    if workspace:
        assert workspace not in output
    assert "invalid_workspace" in output


def test_assertion_prerequisite_status_is_nonzero(monkeypatch):
    from app.evaluation import acceptance
    monkeypatch.setattr(acceptance, "run_acceptance", lambda settings, workspace: {
        "workspace": workspace, "status": "ok", "assertions": [{"status": "prerequisite_unavailable", "case_id": "a"}],
        "observations": [],
    })
    assert main([]) == 1


def test_optional_capability_skip_is_nonfatal_and_mode_is_forwarded(monkeypatch):
    from app.evaluation import acceptance
    seen = []
    def fake_run(settings, workspace, mode):
        seen.append(mode)
        return {"workspace": workspace, "status": "ok", "assertions": [{"status": "passed"}],
                "observations": [{"status": "skipped", "mode": mode}]}
    monkeypatch.setattr(acceptance, "run_acceptance", fake_run)
    assert main(["--mode", "semantic"]) == 0
    assert seen == ["semantic"]


def test_invalid_mode_is_rejected():
    with pytest.raises(SystemExit):
        main(["--mode", "invalid"])
