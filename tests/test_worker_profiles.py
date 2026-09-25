import pytest

from app.autonomy.profiles import PROFILES, get_profile
from app.autonomy.synthesis import WorkerResultContract, synthesize


def test_registered_profiles_are_bounded_and_read_only() -> None:
    assert {"code_analyst", "test_analyst", "architecture_analyst", "security_analyst", "researcher", "verifier"} <= set(PROFILES)
    assert all(profile.read_only for profile in PROFILES.values())
    with pytest.raises(ValueError, match="unknown worker profile"):
        get_profile("invented")


def test_synthesis_preserves_missing_worker_and_completed_findings() -> None:
    results = [
        WorkerResultContract(worker_type="architecture_analyst", task_id="a", status="completed", summary="Runtime wiring exists.", evidence_refs=["trace-a"]),
        WorkerResultContract(worker_type="test_analyst", task_id="b", status="failed", summary="", evidence_refs=[]),
    ]
    synthesis = synthesize(results)
    assert "Runtime wiring exists." in synthesis.findings
    assert synthesis.requires_more_work is True
    assert synthesis.unresolved_gaps
