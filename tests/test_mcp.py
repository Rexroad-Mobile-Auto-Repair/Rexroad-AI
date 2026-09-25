from app.mcp.service import MCPAdapter, MCPServerConfig


class FakeMCP:
    def connect(self): pass
    def close(self): pass
    def list_tools(self): return [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}}]
    def list_resources(self): return [{"uri": "memo://one", "name": "One", "mimeType": "text/plain"}]
    def call_tool(self, name, arguments): return {"name": name, "arguments": arguments}
    def read_resource(self, uri): return {"text": "resource content", "mimeType": "text/plain"}


def test_mcp_lifecycle_discovery_invocation_and_resource():
    adapter = MCPAdapter([MCPServerConfig(name="local")])
    status = adapter.connect("local", FakeMCP())
    assert status.connected is True
    assert adapter.tools()[0].name == "mcp.local.echo"
    assert adapter.resources()[0].uri == "memo://one"
    assert adapter.call("mcp.local.echo", {"value": "x"}).is_error is False
    assert adapter.read("mcp.local.memo://one").content == "resource content"
    adapter.disconnect("local")
    assert adapter.statuses()[0].connected is False


def test_high_risk_mcp_tool_is_not_invocable():
    adapter = MCPAdapter([MCPServerConfig(name="unsafe", read_only=False)])
    adapter.connect("unsafe", FakeMCP())
    assert adapter.call("mcp.unsafe.echo", {}).is_error is True
