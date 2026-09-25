import sys
from pathlib import Path

from app.mcp.service import MCPAdapter, MCPServerConfig


def test_stdio_mcp_lifecycle_and_bounded_discovery() -> None:
    fixture = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
    adapter = MCPAdapter([MCPServerConfig(name="local", transport="stdio", command=sys.executable, args=[str(fixture)])])
    try:
        status = adapter.connect_configured("local")
        assert status.connected is True
        assert status.tool_count == 1
        assert status.resource_count == 1
        result = adapter.call("mcp.local.echo", {"text": "hello"})
        assert result.is_error is False
        assert "hello" in str(result.output)
        resource = adapter.read("mcp.local.memo://one")
        assert resource.content == "live memo"
        assert resource.mime_type == "text/plain"
    finally:
        adapter.disconnect("local")
