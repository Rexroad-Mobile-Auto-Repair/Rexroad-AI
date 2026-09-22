from __future__ import annotations

import json

from app.context.models import (
    ApprovedEvidence,
    ContextRequest,
    ContextResult,
    ContextTruncation,
    EvidenceDecision,
)
from app.tools.models import ModelMessage


class ContextBudgetError(ValueError):
    """The required provider context cannot fit within the configured budget."""


class ContextBuilder:
    @staticmethod
    def _message_bytes(message: ModelMessage) -> int:
        payload = message.model_dump(mode="json", exclude_none=True)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return len(encoded)

    @staticmethod
    def _prefix_by_bytes(content: str, budget: int) -> str:
        encoded = content.encode("utf-8")
        if len(encoded) <= budget:
            return content
        return encoded[:budget].decode("utf-8", errors="ignore")

    def build(self, request: ContextRequest) -> ContextResult:
        messages: list[ModelMessage] = []
        truncations: list[ContextTruncation] = []

        for index, original in enumerate(request.messages):
            message = original.model_copy(deep=True)
            if message.role == "tool":
                original_bytes = len(message.content.encode("utf-8"))
                bounded = self._prefix_by_bytes(
                    message.content,
                    request.tool_result_byte_budget,
                )
                bounded_bytes = len(bounded.encode("utf-8"))
                if bounded != message.content:
                    message.content = bounded
                    truncations.append(
                        ContextTruncation(
                            message_index=index,
                            original_byte_count=original_bytes,
                            bounded_byte_count=bounded_bytes,
                            reason="tool_result_byte_budget",
                        )
                    )
            messages.append(message)

        required_byte_count = sum(self._message_bytes(message) for message in messages)
        if required_byte_count > request.total_byte_budget:
            raise ContextBudgetError("Model context exceeds configured byte budget")

        evidence_decisions: list[EvidenceDecision] = []
        evidence_sections: list[str] = []
        evidence_bytes = 0
        included_count = 0
        for evidence in request.approved_evidence:
            original_bytes = len(evidence.content.encode("utf-8"))
            if evidence.freshness in {"stale", "missing"}:
                evidence_decisions.append(EvidenceDecision(
                        evidence_id=evidence.evidence_id,
                    status="excluded",
                    reason=evidence.freshness,
                    freshness=evidence.freshness,
                    original_byte_count=original_bytes,
                    bounded_byte_count=0,
                ))
                continue
            if included_count >= request.max_evidence_items:
                evidence_decisions.append(EvidenceDecision(
                    evidence_id=evidence.evidence_id,
                    status="excluded",
                    reason="max_evidence_items",
                    freshness=evidence.freshness,
                    original_byte_count=original_bytes,
                    bounded_byte_count=0,
                ))
                continue

            bounded_content = self._prefix_by_bytes(
                evidence.content,
                request.evidence_content_byte_budget,
            )
            bounded_bytes = len(bounded_content.encode("utf-8"))
            candidate_sections = [
                *evidence_sections,
                self._format_evidence(
                    evidence,
                    bounded_content,
                    len(evidence_sections) + 1,
                ),
            ]
            aggregate_message = ModelMessage(
                role="user",
                content="Retrieved evidence:\n" + "\n".join(candidate_sections),
            )
            candidate_bytes = self._message_bytes(aggregate_message)
            if candidate_bytes > request.total_evidence_byte_budget:
                evidence_decisions.append(EvidenceDecision(
                    evidence_id=evidence.evidence_id,
                    status="excluded",
                    reason="evidence_budget",
                    freshness=evidence.freshness,
                    original_byte_count=original_bytes,
                    bounded_byte_count=bounded_bytes,
                    truncated=bounded_content != evidence.content,
                ))
                continue
            if required_byte_count + candidate_bytes > request.total_byte_budget:
                evidence_decisions.append(EvidenceDecision(
                    evidence_id=evidence.evidence_id,
                    status="excluded",
                    reason="total_context_budget",
                    freshness=evidence.freshness,
                    original_byte_count=original_bytes,
                    bounded_byte_count=bounded_bytes,
                    truncated=bounded_content != evidence.content,
                ))
                continue
            evidence_sections = candidate_sections
            evidence_bytes = candidate_bytes
            included_count += 1
            evidence_decisions.append(EvidenceDecision(
                evidence_id=evidence.evidence_id,
                status="included",
                reason="included",
                freshness=evidence.freshness,
                original_byte_count=original_bytes,
                bounded_byte_count=bounded_bytes,
                truncated=bounded_content != evidence.content,
            ))

        return ContextResult(
            messages=[
                *messages,
                *(
                    [ModelMessage(role="user", content="Retrieved evidence:\n" + "\n".join(evidence_sections))]
                    if evidence_sections
                    else []
                ),
            ],
            byte_count=required_byte_count + evidence_bytes,
            message_count=len(messages) + (1 if evidence_sections else 0),
            truncations=truncations,
            evidence_decisions=evidence_decisions,
        )

    @staticmethod
    def _format_evidence(evidence: ApprovedEvidence, content: str, number: int) -> str:
        symbol = evidence.symbol_name or "(none)"
        safe_content = content.replace("</source-content>", "<\\/source-content>")
        citation = f"citation: {evidence.citation_alias}\n" if evidence.citation_alias else ""
        return (
            f"--- evidence {number} ---\n"
            + citation
            + f"workspace: {evidence.workspace}\n"
            + f"file: {evidence.file_path}\n"
            + f"lines: {evidence.line_start}-{evidence.line_end}\n"
            + f"symbol: {symbol}\n"
            + f"freshness: {evidence.freshness}\n"
            + f"retrieval: {evidence.retrieval_method}\n"
            + f"rank: {evidence.rank}\n"
            "content:\n"
            "<source-content>\n"
            f"{safe_content}\n"
            "</source-content>"
        )
