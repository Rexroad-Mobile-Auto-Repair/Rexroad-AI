from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN = re.compile(r"\[([^\]]+)\]")
_CANONICAL = re.compile(r"E[1-9][0-9]*\Z")


@dataclass(frozen=True)
class CitationParseResult:
    aliases: list[str]
    invalid_tokens: list[str]


def parse_citation_aliases(answer: str) -> CitationParseResult:
    aliases: list[str] = []
    invalid: list[str] = []
    for match in _TOKEN.finditer(answer):
        token = match.group(0)
        inner = match.group(1)
        if _CANONICAL.fullmatch(inner):
            if inner not in aliases:
                aliases.append(inner)
        elif (
            inner.startswith("E")
            or (inner.startswith("e") and len(inner) > 1 and inner[1].isdigit())
        ) and token not in invalid:
            invalid.append(token)
    return CitationParseResult(aliases=aliases, invalid_tokens=invalid)
