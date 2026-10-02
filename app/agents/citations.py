"""Line references derived from the source snapshots actually read this turn."""
import re


def numbered_source(path: str, text: str) -> str:
    return f"Source: {path}\n" + "\n".join(f"{i}: {line}" for i, line in enumerate(text.splitlines(), 1))


def ground_line_references(answer: str, sources: dict[str, str]) -> str:
    cited_paths = re.findall(r"\bsource(?: citation)?(?: is)?\s*:\s*`?([\w./-]+\.(?:py|php|js|ts|tsx|html|css|md))", answer, re.IGNORECASE)
    unknown = [path for path in cited_paths if not any(saved == path or saved.endswith('/' + path) for saved in sources)]
    if unknown:
        answer += "\n\nSource citation limitation: " + ", ".join(sorted(set(unknown))) + " was not read in this request."
    if not sources or not re.search(r"\blines?\s+\d+", answer, re.IGNORECASE):
        return answer
    quoted_text = re.findall(r"`([^`\n]+)`", answer) + re.findall(r'"([^"\n]+)"', answer)
    quotes = {quote.strip() for quote in quoted_text if len(quote.strip()) >= 8}
    matches = {(path, i) for path, text in sources.items() for i, line in enumerate(text.splitlines(), 1)
               if line.strip() in quotes}
    blocks = re.findall(r"```(?:\w+)?\n(.*?)```", answer, re.DOTALL)
    block_ranges = []
    for block in blocks:
        code = block.strip("\n")
        for path, text in sources.items():
            if code and text.count(code) == 1:
                start = text[:text.index(code)].count("\n") + 1
                block_ranges.append((path, start, start + len(code.splitlines()) - 1))
    if len(sources) == 1 and len(block_ranges) == 1 and not matches:
        _, start, end = block_ranges[0]
        return re.sub(r"(ends? at\s+)?\bline\s+\d+\b", lambda m: (m[1] or "") + f"line {end if m[1] else start}", answer, flags=re.IGNORECASE)
    if len(sources) == 1 and len(matches) == 1:
        _, line = next(iter(matches))
        return re.sub(r"\bline\s+\d+\b", f"line {line}", answer, flags=re.IGNORECASE)
    # Do not invent an anchor when no unique quotation supports the reference.
    return re.sub(r"\blines?\s+\d+(?:\s*[-–]\s*\d+)?\b", "the saved source", answer, flags=re.IGNORECASE)
