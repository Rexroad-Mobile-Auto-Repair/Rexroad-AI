from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.subagents import SubAgentResult


class SourceExcerpt(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    text: str = Field(max_length=16000)
    audit_ref: str = Field(min_length=1, max_length=100)
    truncated: bool = False


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
    source_evidence: list[SourceExcerpt] = Field(default_factory=list, max_length=10)


class TeamSynthesis(BaseModel):
    findings: list[str] = Field(default_factory=list, max_length=50)
    agreements: list[str] = Field(default_factory=list, max_length=50)
    disagreements: list[str] = Field(default_factory=list, max_length=50)
    unresolved_gaps: list[str] = Field(default_factory=list, max_length=50)
    recommended_action: str | None = Field(default=None, max_length=1000)
    requires_more_work: bool = False
    reconciliation_status: Literal["not_required", "pending", "completed", "failed"] = "not_required"


def synthesize(results: list[WorkerResultContract]) -> TeamSynthesis:
    summaries = [item.summary for item in results if item.status == "completed"]
    failed = [item for item in results if item.status != "completed"]
    gaps = [f"Worker {item.task_id} ({item.worker_type}) did not complete." for item in failed]
    pending = len(summaries) > 1
    if pending:
        gaps.append("Cross-worker findings have not been reconciled.")
    return TeamSynthesis(findings=summaries[:50], unresolved_gaps=gaps[:50], requires_more_work=bool(gaps),
                         reconciliation_status="pending" if pending else "not_required")


def from_subagent(result: SubAgentResult, evidence_refs: list[str] | None = None) -> WorkerResultContract:
    return WorkerResultContract(worker_type=result.worker_profile, task_id=result.task_id, status=result.status, summary=result.summary[:4000], evidence_refs=(evidence_refs or [])[:20])
