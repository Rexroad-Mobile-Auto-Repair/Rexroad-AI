from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from app.tools.models import ToolSpec

ToolPermission = Literal["read", "propose", "plan_create", "plan_write"]

@dataclass(frozen=True)
class ToolAuthorization:
    token: str
    tool: str
    permission: ToolPermission
    scope: str
    session_id: str | None = None

@dataclass(frozen=True)
class ToolApproval:
    token: str
    authorization_token: str
    tool: str
    scope: str
    session_id: str | None
    arguments_fingerprint: str


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    permission: ToolPermission
    handler: Callable[..., Any]
    parameters: dict[str, Any] = field(default_factory=dict)
    high_impact: bool = False


class ToolNotRegisteredError(KeyError):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._authorizations: dict[str, ToolAuthorization] = {}
        self._approvals: dict[str, ToolApproval] = {}

    def authorize(self, name: str, scope: str, session_id: str | None = None) -> ToolAuthorization:
        tool = self.get(name)
        authorization = ToolAuthorization(str(uuid4()), name, tool.permission, scope, session_id)
        self._authorizations[authorization.token] = authorization
        return authorization

    def validate_authorization(self, authorization: ToolAuthorization, name: str, scope: str, session_id: str | None = None) -> bool:
        return self._authorizations.get(authorization.token) == authorization and authorization.tool == name and authorization.scope == scope and authorization.session_id == session_id

    def approve(self, authorization: ToolAuthorization, arguments: dict[str, Any]) -> ToolApproval:
        tool = self.get(authorization.tool)
        if not self.validate_authorization(authorization, tool.name, authorization.scope, authorization.session_id):
            raise ValueError("invalid tool authorization")
        fingerprint = hashlib.sha256(json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        approval = ToolApproval(str(uuid4()), authorization.token, tool.name, authorization.scope, authorization.session_id, fingerprint)
        self._approvals[approval.token] = approval
        return approval

    def consume_approval(self, approval: ToolApproval, authorization: ToolAuthorization, name: str, scope: str, session_id: str | None, arguments: dict[str, Any]) -> bool:
        if not self.get(name).high_impact:
            return True
        fingerprint = hashlib.sha256(json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        valid = self._approvals.get(approval.token) == approval and approval.authorization_token == authorization.token and approval.tool == name and approval.scope == scope and approval.session_id == session_id and approval.arguments_fingerprint == fingerprint
        if valid:
            del self._approvals[approval.token]
        return valid

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
