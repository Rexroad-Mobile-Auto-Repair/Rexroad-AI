from __future__ import annotations

import re


def requested_test_paths(instruction: str) -> list[str]:
    """Recognize explicitly named Python tests only when a test change is requested."""
    request = r"\b(?:add|update|extend|write|include)\s+(?:(?:one|two|three|four|five|[0-9]+|a|an|focused|regression|meaningful|new|the|existing)\s+)*(?:test|tests)\b"
    positive = any(re.search(request, sentence, re.IGNORECASE) and not re.search(r"\b(?:do not|don't|no need to)\b", sentence, re.IGNORECASE)
                   for sentence in re.split(r"[.!?]\s+|\n", instruction))
    if not positive:
        return []
    paths = re.findall(r"(?<![\w/])(?:tests/[\w./-]+\.py|test_[\w-]+\.py)\b", instruction.replace("\\", "/"))
    return list(dict.fromkeys(paths))[:5]


def claims_failed_checks(summary: str) -> bool:
    """Flag direct failure claims, leaving historical and negated descriptions alone."""
    for sentence in re.split(r"[.!?]\s+|\n", summary):
        if re.search(r"\b(?:previous|earlier|original|historical|no failures?|no (?:tests?|checks?) failed|did not fail|never failed|without failures?|do not|don't)\b", sentence, re.IGNORECASE):
            continue
        if re.search(r"\b(?:pytest|tests?|checks?|test suite)\s+(?:has |have |is |are |currently |still )*(?:failed|failing|fails|failures?)\b|\b(?:failed|failing)\s+(?:tests?|checks?)\b", sentence, re.IGNORECASE):
            return True
    return False
