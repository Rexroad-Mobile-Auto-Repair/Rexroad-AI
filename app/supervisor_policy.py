from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SupervisorRecommendationRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=4000)
    scope: str | None = None
    workspace: str | None = None
    task_kind: str | None = Field(default=None, max_length=80)


class SupervisorRecommendation(BaseModel):
    action: Literal["direct", "delegate"]
    profile: str | None = None
    category: str
    reason: str = Field(max_length=500)
    prerequisites: list[str] = Field(default_factory=list, max_length=8)
    scope: str | None = None
    workspace: str | None = None


class SupervisorPolicy:
    def recommend(self, request: SupervisorRecommendationRequest) -> SupervisorRecommendation:
        text = request.instruction.casefold()
        kind = (request.task_kind or "").casefold()
        if text.startswith("verify completed work"):
            return SupervisorRecommendation(action="delegate", profile="verifier", category="verification", reason="The task requests an independent verification check.", prerequisites=["completed result"], scope=request.scope, workspace=request.workspace)
        if text.startswith("inspect code"):
            return SupervisorRecommendation(action="delegate", profile="code_analyst", category="code_analysis", reason="The task explicitly requests source-code inspection.", prerequisites=["explicit workspace"] if not request.workspace else [], scope=request.scope, workspace=request.workspace)
        if kind in {"research", "comparison"} or any(term in text for term in ("research", "compare sources", "find evidence", "investigate")):
            return SupervisorRecommendation(action="delegate", profile="researcher", category="research", reason="The task is primarily research or evidence gathering.", prerequisites=["explicit scope"] if not request.scope else [], scope=request.scope, workspace=request.workspace)
        if kind in {"code", "analysis"} or any(term in text for term in ("inspect code", "analyze dependency", "review implementation", "source code")):
            return SupervisorRecommendation(action="delegate", profile="code_analyst", category="code_analysis", reason="The task requires focused source-code inspection.", prerequisites=["explicit workspace"] if not request.workspace else [], scope=request.scope, workspace=request.workspace)
        if kind in {"verify", "verification"} or any(term in text for term in ("verify", "validate result", "check completed")):
            return SupervisorRecommendation(action="delegate", profile="verifier", category="verification", reason="The task requests an independent verification check.", prerequisites=["completed result"] , scope=request.scope, workspace=request.workspace)
        return SupervisorRecommendation(action="direct", category="direct", reason="The task does not match a specialized worker category.", scope=request.scope, workspace=request.workspace)
