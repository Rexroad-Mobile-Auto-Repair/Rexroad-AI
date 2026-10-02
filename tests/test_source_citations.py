from app.agents.citations import ground_line_references, numbered_source


def test_wrong_line_is_corrected_only_from_unique_exact_quote():
    sources = {"models.py": "class AgentEvent:\n    sequence: int\n"}
    assert ground_line_references("models.py, line 43: `sequence: int`", sources) == "models.py, line 2: `sequence: int`"
    assert ground_line_references('models.py, line 43: "sequence: int"', sources) == 'models.py, line 2: "sequence: int"'


def test_ambiguous_or_unquoted_reference_is_not_invented():
    sources = {"a.py": "    sequence: int\n    sequence: int\n"}
    assert "line 43" not in ground_line_references("line 43: `sequence: int`", sources)
    assert "line 43" not in ground_line_references("a.py, line 43", sources)


def test_numbered_snapshot_preserves_blank_lines_and_source_identity():
    assert numbered_source("a.py", "a\n\nb\n") == "Source: a.py\n1: a\n2: \n3: b"


def test_contiguous_code_block_has_verified_start_and_end():
    source = "# header\nclass AgentEvent:\n    sequence: int\n"
    answer = "```python\nclass AgentEvent:\n    sequence: int\n```\nStarts at line 43 and ends at line 45."
    result = ground_line_references(answer, {"models.py": source})
    assert "Starts at line 2" in result and "ends at line 3" in result


def test_explicit_unread_source_path_is_flagged():
    assert "was not read" in ground_line_references("Source: invented.py", {"real.py": "x"})
    assert "was not read" not in ground_line_references("Source: real.py", {"app/real.py": "x"})
