from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.tools.models import ToolSpec

ToolPermission = Literal["read", "propose", "plan_write"]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    permission: ToolPermission
    handler: Callable[..., Any]
    parameters: dict[str, Any] = field(default_factory=dict)


class ToolNotRegisteredError(KeyError):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(
                f"Tool already registered: {tool.name}"
            )

        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotRegisteredError(name) from exc

    def names(self) -> list[str]:
        return sorted(self._tools)

    def definitions(self) -> list[ToolDefinition]:
        return [
            self._tools[name]
            for name in self.names()
        ]

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
            )
            for tool in self.definitions()
        ]

    def execute(
        self,
        name: str,
        **arguments: Any,
    ) -> Any:
        tool = self.get(name)

        return tool.handler(**arguments)
