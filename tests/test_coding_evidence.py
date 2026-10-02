import hashlib
from types import SimpleNamespace

import pytest

from app.coding_evidence import verification_conflicts
from app.coding_workflows import CodingWorkflowCreate
from app.policy.workspaces import WorkspaceRegistry
from tests.test_coding_workflows import _service


def _evidence(tmp_path, summary="Current implementation is correct. Tests passed."):
    file = tmp_path / "example.py"
    file.write_bytes(b"VALUE = 2\r\n")
    text = file.read_text(encoding="utf-8")
    workflow = SimpleNamespace(scope="s", workspace="ws", verifier_task_id="v", mutation_spec_ids=["patch"], mutation_trace_ids=["p-trace"], check_spec_ids=["check"], check_trace_ids=["c-trace"])
    specs = {"patch": SimpleNamespace(plan_id="p", step_id="patch-step", arguments={"relative_path": "example.py"}), "check": SimpleNamespace(plan_id="p", step_id="check-step")}
    traces = {"p-trace": SimpleNamespace(tool="filesystem.apply_patch", plan_id="p", step_id="patch-step", status="success", result_preview=str({"after_hash": hashlib.sha256(text.encode()).hexdigest()})), "c-trace": SimpleNamespace(tool="workspace.run_check", plan_id="p", step_id="check-step", status="success", result_preview=str({"passed": True}))}
    sources = [{"path": "example.py", "text": text, "truncated": False}]
    agents = SimpleNamespace(provider_backed=True, get=lambda _: (SimpleNamespace(scope="s", workspace="ws"), SimpleNamespace(status="completed", summary=summary)), source_evidence=lambda *args: sources)
    return workflow, WorkspaceRegistry({"ws": tmp_path}), SimpleNamespace(get=lambda key, scope: specs[key]), SimpleNamespace(get=lambda key, scope: traces[key]), agents, sources, traces


@pytest.mark.parametrize("summary", ["Tests failed.", "Pytest is failing.", "The test suite failed."])
def test_false_test_failure_claim_is_flagged_against_successful_executor(tmp_path, summary):
    evidence = _evidence(tmp_path, summary)
    conflicts = verification_conflicts(*evidence[:5])
    assert len(conflicts) == 1 and "claims failed checks" in conflicts[0]


@pytest.mark.parametrize("summary", ["Tests passed.", "No tests failed.", "The earlier tests failed. The current tests passed."])
def test_current_success_and_historical_failure_are_not_false_alarms(tmp_path, summary):
    assert verification_conflicts(*_evidence(tmp_path, summary)[:5]) == []


def test_stale_file_and_truncated_verifier_source_block_acceptance(tmp_path):
    evidence = _evidence(tmp_path)
    evidence[5][0]["truncated"] = True
    assert any("complete current source" in x for x in verification_conflicts(*evidence[:5]))
    (tmp_path / "example.py").write_text("VALUE = 3\n", encoding="utf-8")
    assert any("no longer matches" in x for x in verification_conflicts(*evidence[:5]))


def test_wrong_check_trace_cannot_certify_the_workflow(tmp_path):
    evidence = _evidence(tmp_path)
    evidence[6]["c-trace"].step_id = "unrelated-step"
    assert any("checks are missing" in x for x in verification_conflicts(*evidence[:5]))


def test_service_rejects_conflicting_acceptance_but_allows_rejection(tmp_path):
    evidence = _evidence(tmp_path, "Tests failed.")
    service = _service(tmp_path)
    item = service.create(CodingWorkflowCreate(scope="s", workspace="ws", instruction="edit"))
    service._save(item.model_copy(update={"status": "awaiting_verifier_review", "verifier_task_id": "v", "mutation_spec_ids": ["patch"], "mutation_trace_ids": ["p-trace"], "check_spec_ids": ["check"], "check_trace_ids": ["c-trace"]}))
    service.specs, service.traces, service.agents = evidence[2:5]
    reviews = []
    service.agents.review = lambda *args: reviews.append(args) or SimpleNamespace(status=args[2])
    with pytest.raises(ValueError, match="Verification conflicts"):
        service.review_verifier(item.workflow_id, "s", "accepted")
    assert reviews == []
    assert service.get(item.workflow_id, "s").status == "awaiting_verifier_review"
    assert service.review_verifier(item.workflow_id, "s", "rejected").outcome == "rejected"
