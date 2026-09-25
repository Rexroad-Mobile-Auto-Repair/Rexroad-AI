from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

MAX_PAYLOAD = 20_000


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    transport: str = Field(default="fake", pattern=r"^fake$")
    read_only: bool = True


class MCPToolDefinition(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    risk: str = Field(default="unknown", pattern=r"^(read_only|high_risk|unknown)$")
    server: str


class MCPResourceDefinition(BaseModel):
    uri: str
    name: str = ""
    description: str = ""
    mime_type: str | None = None
    server: str


class MCPServerStatus(BaseModel):
    name: str
    configured: bool
    connected: bool = False
    tool_count: int = 0
    resource_count: int = 0
    last_error: str | None = None
    latency_ms: int | None = None


class MCPToolResult(BaseModel):
    server: str
    tool: str
    output: Any = None
    is_error: bool = False


class MCPResourceReadResult(BaseModel):
    server: str
    uri: str
    mime_type: str | None = None
    content: str
    retrieved_at: datetime


class MCPClient(Protocol):
    def connect(self) -> None: ...
    def close(self) -> None: ...
    def list_tools(self) -> list[dict[str, Any]]: ...
    def list_resources(self) -> list[dict[str, Any]]: ...
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...
    def read_resource(self, uri: str) -> dict[str, Any]: ...


class MCPAdapter:
    def __init__(self, configs: list[MCPServerConfig] | None = None) -> None:
        self._configs = {config.name: config for config in configs or []}
        self._clients: dict[str, MCPClient] = {}
        self._tools: dict[str, MCPToolDefinition] = {}
        self._resources: dict[str, MCPResourceDefinition] = {}
        self._status: dict[str, MCPServerStatus] = {
            name: MCPServerStatus(name=name, configured=True) for name in self._configs
        }

    def connect(self, name: str, client: MCPClient) -> MCPServerStatus:
        if name not in self._configs:
            raise ValueError("MCP server is not configured")
        started = time.perf_counter()
        try:
            client.connect()
            self._clients[name] = client
            tools = []
            for item in client.list_tools()[:100]:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    continue
                key = f"mcp.{name}.{item['name']}"
                if key in self._tools:
                    continue
                self._tools[key] = MCPToolDefinition(
                    name=key, server=name, description=str(item.get("description", ""))[:500],
                    input_schema=item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {},
                    risk="read_only" if self._configs[name].read_only else "high_risk",
                )
                tools.append(key)
            resources = []
            for item in client.list_resources()[:100]:
                if not isinstance(item, dict) or not isinstance(item.get("uri"), str):
                    continue
                key = f"mcp.{name}.{item['uri']}"
                self._resources[key] = MCPResourceDefinition(uri=item["uri"], server=name, name=str(item.get("name", ""))[:200], description=str(item.get("description", ""))[:500], mime_type=item.get("mimeType"))
                resources.append(key)
            self._status[name] = MCPServerStatus(name=name, configured=True, connected=True, tool_count=len(tools), resource_count=len(resources), latency_ms=round((time.perf_counter() - started) * 1000))
            return self._status[name]
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self._status[name] = MCPServerStatus(name=name, configured=True, last_error=str(exc)[:300])
            return self._status[name]

    def disconnect(self, name: str) -> None:
        client = self._clients.pop(name, None)
        if client:
            client.close()
        self._status[name] = MCPServerStatus(name=name, configured=True)

    def statuses(self) -> list[MCPServerStatus]:
        return list(self._status.values())

    def tools(self) -> list[MCPToolDefinition]:
        return list(self._tools.values())

    def register_tools(self, registry: Any) -> None:
        """Register only connected read-only capabilities; native names win."""
        from app.tools.registry import ToolDefinition
        for definition in self._tools.values():
            if definition.risk != "read_only" or definition.name in registry.names():
                continue
            def invoke(_definition=definition, **arguments):
                return self.call(_definition.name, arguments).model_dump(mode="json")
            registry.register(ToolDefinition(
                name=definition.name,
                description=f"MCP read-only tool from configured server {definition.server}: {definition.description}",
                permission="read",
                handler=invoke,
                parameters=definition.input_schema,
            ))

    def resources(self) -> list[MCPResourceDefinition]:
        return list(self._resources.values())

    def call(self, qualified_name: str, arguments: dict[str, Any]) -> MCPToolResult:
        definition = self._tools.get(qualified_name)
        if definition is None or definition.risk != "read_only":
            return MCPToolResult(server=definition.server if definition else "unknown", tool=qualified_name, output={"error": "MCP tool is unavailable or high-risk"}, is_error=True)
        if len(str(arguments)) > MAX_PAYLOAD:
            return MCPToolResult(server=definition.server, tool=qualified_name, output={"error": "arguments exceed limit"}, is_error=True)
        try:
            output = self._clients[definition.server].call_tool(qualified_name.split(f"mcp.{definition.server}.", 1)[1], arguments)
            return MCPToolResult(server=definition.server, tool=qualified_name, output=str(output)[:MAX_PAYLOAD])
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return MCPToolResult(server=definition.server, tool=qualified_name, output={"error": str(exc)[:300]}, is_error=True)

    def read(self, qualified_resource: str) -> MCPResourceReadResult:
        resource = self._resources[qualified_resource]
        result = self._clients[resource.server].read_resource(resource.uri)
        content = str(result.get("text", result.get("content", "")))[:MAX_PAYLOAD]
        return MCPResourceReadResult(server=resource.server, uri=resource.uri, mime_type=result.get("mimeType", resource.mime_type), content=content, retrieved_at=datetime.now(UTC))
