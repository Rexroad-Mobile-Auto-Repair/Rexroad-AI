from __future__ import annotations

from pydantic import BaseModel, Field

from app.subagents import SubAgentResult


class WorkerResultContract(BaseModel):
    worker_type: str
    task_id: str
    status: str
    summary: str = Field(max_length=4000)
    findings: list[str] = Field(default_factory=list, max_length=20)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    files_examined: list[str] = Field(default_factory=list, max_length=50)
    symbols_examined: list[str] = Field(default_factory=list, max_length=50)
    risks: list[str] = Field(default_factory=list, max_length=20)
    recommendations: list[str] = Field(default_factory=list, max_length=20)


class TeamSynthesis(BaseModel):
    findings: list[str] = Field(default_factory=list, max_length=50)
    agreements: list[str] = Field(default_factory=list, max_length=50)
    disagreements: list[str] = Field(default_factory=list, max_length=50)
    unresolved_gaps: list[str] = Field(default_factory=list, max_length=50)
    recommended_action: str | None = Field(default=None, max_length=1000)
    requires_more_work: bool = False


def synthesize(results: list[WorkerResultContract]) -> TeamSynthesis:
    summaries = [item.summary for item in results if item.status == "completed"]
    disagreements = [item.summary for item in results if item.status != "completed"]
    return TeamSynthesis(findings=summaries[:50], agreements=summaries[:50] if len(summaries) < 2 else ["Multiple workers completed independently."], disagreements=disagreements[:50], unresolved_gaps=["A worker result is unavailable."] if disagreements else [], requires_more_work=bool(disagreements))


def from_subagent(result: SubAgentResult, evidence_refs: list[str] | None = None) -> WorkerResultContract:
    return WorkerResultContract(worker_type=result.worker_profile, task_id=result.task_id, status=result.status, summary=result.summary[:4000], evidence_refs=(evidence_refs or [])[:20])
