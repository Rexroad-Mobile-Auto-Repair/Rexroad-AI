from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.mcp.service import MCPServerConfig


def load_mcp_server_configs(path: Path) -> list[MCPServerConfig]:
    """Load an optional explicit MCP config; absent/invalid config fails closed."""
    if not path.is_file():
        return []
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        items = raw.get("servers", []) if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return []
        return [MCPServerConfig.model_validate(item) for item in items[:20] if isinstance(item, dict)]
    except (OSError, TypeError, ValueError):
        return []
