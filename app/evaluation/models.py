from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

EvaluationStatus = Literal["passed", "failed", "skipped", "error"]
RetrievalMode = Literal["lexical", "semantic", "hybrid"]


class RetrievalBenchmarkCase(BaseModel):
    case_id: str
    workspace: str
    query: str
    mode: RetrievalMode = "lexical"
    top_k: int = Field(ge=1, le=50)
    expected_evidence_id: str | None = None
    expected_file_path: str | None = None
    expected_symbol_name: str | None = None
    expected_freshness: Literal["current", "stale", "missing"] | None = None
    max_rank: int | None = Field(default=None, ge=1)

    @field_validator("case_id", "workspace", "query")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("expected_evidence_id", "expected_symbol_name")
    @classmethod
    def _clean_optional_identity(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\r" in value or "\n" in value):
            raise ValueError("identity values must be nonblank and single-line")
        return value

    @field_validator("expected_file_path")
    @classmethod
    def _relative_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.replace("\\", "/")
        if (
            not normalized.strip()
            or normalized.startswith("/")
            or (len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/")
            or "\r" in normalized
            or "\n" in normalized
            or any(part == ".." for part in normalized.split("/"))
        ):
            raise ValueError("expected_file_path must be relative and safe")
        return normalized

    @model_validator(mode="after")
    def _validate_expectations(self) -> "RetrievalBenchmarkCase":
        if (self.expected_evidence_id is None) == (self.expected_file_path is None):
            raise ValueError("exactly one primary result identity is required")
        if self.max_rank is not None and self.max_rank > self.top_k:
            raise ValueError("max_rank must not exceed top_k")
        if self.expected_symbol_name is not None and self.expected_file_path is None:
            raise ValueError("expected_symbol_name requires expected_file_path")
        return self


class RetrievalCaseResult(BaseModel):
    case_id: str
    status: EvaluationStatus
    expected_evidence_id: str | None = None
    expected_file_path: str | None = None
    matched_evidence_id: str | None = None
    matched_file_path: str | None = None
    observed_rank: int | None = None
    retrieval_method: str | None = None
    observed_freshness: str | None = None
    reason: str | None = None
    ordered_result_ids: list[str] = Field(default_factory=list)
    repeatable: bool | None = None
    duration_ms: float | None = None


class EvaluationSuiteResult(BaseModel):
    suite_id: str
    cases: list[RetrievalCaseResult]
    total: int
    passed: int
    failed: int
    skipped: int
    errors: int
    pass_rate: float
