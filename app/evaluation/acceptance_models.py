from typing import Literal

from pydantic import BaseModel, Field, field_validator

AcceptanceCaseKind = Literal["assertion", "observation"]


class AcceptanceObservationCase(BaseModel):
    case_id: str
    workspace: str
    query: str
    top_k: int
    target_file_path: str
    target_symbol_name: str | None = None

    @field_validator("case_id", "workspace", "query", "target_file_path", "target_symbol_name")
    @classmethod
    def _safe_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or "\r" in value or "\n" in value:
            raise ValueError("value must be nonblank and single-line")
        return value

    @field_validator("target_file_path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if (normalized.startswith("/") or
                (len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/") or
                any(part == ".." for part in normalized.split("/"))):
            raise ValueError("target_file_path must be relative and safe")
        return normalized

    @field_validator("top_k")
    @classmethod
    def _valid_limit(cls, value: int) -> int:
        if not 1 <= value <= 50:
            raise ValueError("top_k must be between 1 and 50")
        return value


class AcceptanceObservationResult(BaseModel):
    case_id: str
    workspace: str
    mode: Literal["lexical"] = "lexical"
    status: Literal["observed", "prerequisite_unavailable", "error"]
    target_file_path: str
    target_symbol_name: str | None = None
    observed_rank: int | None = None
    matched_file_path: str | None = None
    matched_symbol_name: str | None = None
    freshness: str | None = None
    repeatable: bool | None = None
    duration_ms: float | None = None
    safe_reason: str | None = None
    ordered_result_ids: list[str] = Field(default_factory=list)
