from app.tools.output_policy import sanitize_output


def test_redacts_nested_sensitive_keys_without_mutating_input():
    value = {"id": "keep", "API_KEY": "secret", "nested": {"password": "hidden", "name": "ok"}}
    safe = sanitize_output(value)
    assert safe == {"id": "keep", "API_KEY": "[redacted]", "nested": {"password": "[redacted]", "name": "ok"}}
    assert value["API_KEY"] == "secret"


def test_bounds_strings_collections_and_depth_deterministically():
    safe = sanitize_output({"text": "x" * 3000, "items": list(range(200)), "deep": {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}})
    assert len(safe["text"]) < 2100
    assert len(safe["items"]) == 100
    assert safe["deep"]["a"]["b"]["c"]["d"]["e"] == "[truncated]"
