import pytest

from app.supervisor_policy import SupervisorPolicy, SupervisorRecommendationRequest


@pytest.mark.parametrize(("instruction", "profile"), [("research this issue", "researcher"), ("inspect source code dependencies", "code_analyst"), ("verify completed result", "verifier")])
def test_specialized_recommendations(instruction, profile):
    result = SupervisorPolicy().recommend(SupervisorRecommendationRequest(instruction=instruction, scope="s", workspace="w"))
    assert result.action == "delegate"
    assert result.profile == profile


def test_direct_ambiguous_and_deterministic():
    policy = SupervisorPolicy()
    request = SupervisorRecommendationRequest(instruction="summarize this", scope="s")
    assert policy.recommend(request) == policy.recommend(request)
    assert policy.recommend(request).action == "direct"


def test_empty_instruction_rejected():
    with pytest.raises(ValueError):
        SupervisorRecommendationRequest(instruction="")
