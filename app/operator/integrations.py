import threading


class IntegrationControls:
    def __init__(self, adapter, tools):
        self.adapter, self.tools = adapter, tools
        self.lock = threading.Lock()

    def catalog(self):
        return [
            {
                "name": s.name,
                "connected": s.connected,
                "configured": s.configured,
                "tool_count": s.tool_count,
                "resource_count": s.resource_count,
                "last_error": "Connection failed; check the trusted server configuration"
                if s.last_error
                else None,
                "tools": [
                    {
                        "name": t.name,
                        "enabled": t.risk == "read_only" and s.connected,
                        "risk": t.risk,
                    }
                    for t in self.adapter.tools()
                    if t.server == s.name
                ],
            }
            for s in self.adapter.statuses()
        ]

    def action(self, name, action):
        if action not in {"connect", "disconnect"} or name not in {
            s.name for s in self.adapter.statuses()
        }:
            raise ValueError("Choose a configured integration and a supported action")
        with self.lock:
            if (
                action == "connect"
                and next(s for s in self.adapter.statuses() if s.name == name).connected
            ):
                return next(s for s in self.catalog() if s["name"] == name)
            for tool in self.tools.names():
                if tool.startswith("mcp." + name + "."):
                    self.tools.unregister(tool)
            self.adapter.disconnect(name)
            if action == "connect":
                self.adapter.connect_configured(name)
                self.adapter.register_tools(self.tools)
            return next(s for s in self.catalog() if s["name"] == name)
