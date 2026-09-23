from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    reason: str


class VerificationPolicy(Protocol):
    def verify(self, result: Any) -> VerificationResult: ...


@dataclass(frozen=True)
class ResultPresentPolicy:
    def verify(self, result: Any) -> VerificationResult:
        return VerificationResult(result is not None, "result_present" if result is not None else "result_missing")


@dataclass(frozen=True)
class FieldEqualsPolicy:
    field: str
    expected: Any

    def __post_init__(self) -> None:
        if not self.field.strip() or "\n" in self.field or "\r" in self.field:
            raise ValueError("field must be a single nonblank line")

    def verify(self, result: Any) -> VerificationResult:
        actual = result.get(self.field) if isinstance(result, dict) else None
        return VerificationResult(actual == self.expected, "field_match" if actual == self.expected else "field_mismatch")


@dataclass(frozen=True)
class TextContainsPolicy:
    text: str

    def __post_init__(self) -> None:
        if not self.text.strip() or "\n" in self.text or "\r" in self.text:
            raise ValueError("text must be a single nonblank line")

    def verify(self, result: Any) -> VerificationResult:
        passed = self.text in str(result) if result is not None else False
        return VerificationResult(passed, "text_match" if passed else "text_missing")
