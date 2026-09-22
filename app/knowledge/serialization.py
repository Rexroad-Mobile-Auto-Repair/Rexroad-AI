from __future__ import annotations

import json
from typing import Any

from app.knowledge.models import KnowledgeSearchResult


def serialize_search_results(results: Any) -> str:
    if not isinstance(results, list) or not all(
        isinstance(result, KnowledgeSearchResult) for result in results
    ):
        raise TypeError("knowledge.search returned an unsupported result")

    payload: list[dict[str, Any]] = []
    for result in results:
        chunk = result.chunk
        evidence = result.evidence
        item: dict[str, Any] = {
            "content": chunk.content,
            "workspace": evidence.workspace,
            "relative_file_path": evidence.file_path,
            "line_start": evidence.line_start,
            "line_end": evidence.line_end,
            "retrieval_method": evidence.retrieval_method,
            "rank": evidence.rank,
            "freshness": evidence.freshness,
        }
        if evidence.symbol_name is not None:
            item["symbol_name"] = evidence.symbol_name
        if evidence.symbol_type is not None:
            item["symbol_type"] = evidence.symbol_type
        payload.append(item)

    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
