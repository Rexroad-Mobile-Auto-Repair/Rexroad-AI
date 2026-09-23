from __future__ import annotations

from typing import Any

SENSITIVE_KEYS = {"token", "api_key", "password", "secret", "authorization", "cookie"}
MAX_STRING = 2000
MAX_ITEMS = 100
MAX_DEPTH = 6


def sanitize_output(value: Any, *, _depth: int = 0) -> Any:
    if _depth >= MAX_DEPTH:
        return "[truncated]"
    if isinstance(value, str):
        return value if len(value) <= MAX_STRING else value[:MAX_STRING] + "...[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        return {str(key): "[redacted]" if str(key).lower() in SENSITIVE_KEYS else sanitize_output(item, _depth=_depth + 1) for key, item in list(value.items())[:MAX_ITEMS]}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_output(item, _depth=_depth + 1) for item in list(value)[:MAX_ITEMS]]
    return sanitize_output(str(value), _depth=_depth + 1)
