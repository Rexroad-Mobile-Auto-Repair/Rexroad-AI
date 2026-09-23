from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from app.tools.models import ToolSpec

ToolPermission = Literal["read", "propose", "plan_create", "plan_write", "filesystem_write", "workspace_check"]

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
class ToolApprovalRequest:
    id: str
    tool: str
    scope: str
    session_id: str | None
    arguments_fingerprint: str
    summary: str
    status: Literal["pending", "approved", "rejected", "consumed"]


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
    def __init__(self, database_path: str | Path | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._authorizations: dict[str, ToolAuthorization] = {}
        self._approvals: dict[str, ToolApproval] = {}
        self._approval_requests: dict[str, tuple[ToolApprovalRequest, ToolApproval | None]] = {}
        self._database_path = Path(database_path) if database_path else None
        if self._database_path:
            with sqlite3.connect(self._database_path) as connection:
                connection.execute("CREATE TABLE IF NOT EXISTS tool_approval_requests (id TEXT PRIMARY KEY, tool TEXT NOT NULL, scope TEXT NOT NULL, session_id TEXT, arguments_fingerprint TEXT NOT NULL, summary TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")

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

    def request_approval(self, authorization: ToolAuthorization, arguments: dict[str, Any], summary: str) -> ToolApprovalRequest:
        tool = self.get(authorization.tool)
        if not tool.high_impact or not self.validate_authorization(authorization, tool.name, authorization.scope, authorization.session_id):
            raise ValueError("approval request not allowed")
        fingerprint = hashlib.sha256(json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        request = ToolApprovalRequest(str(uuid4()), tool.name, authorization.scope, authorization.session_id, fingerprint, summary[:500], "pending")
        self._approval_requests[request.id] = (request, None)
        self._persist_request(request)
        return request

    def review_approval(self, request_id: str, scope: str, approve: bool) -> ToolApprovalRequest:
        current = self._approval_requests.get(request_id)
        if current is None or current[0].scope != scope:
            raise KeyError(request_id)
        request, capability = current
        if request.status != "pending":
            return request
        status = "approved" if approve else "rejected"
        updated = ToolApprovalRequest(request.id, request.tool, request.scope, request.session_id, request.arguments_fingerprint, request.summary, status)
        if approve:
            auth = self._authorizations[next(token for token, item in self._authorizations.items() if item.tool == request.tool and item.scope == request.scope and item.session_id == request.session_id)]
            capability = ToolApproval(str(uuid4()), auth.token, request.tool, request.scope, request.session_id, request.arguments_fingerprint)
            self._approvals[capability.token] = capability
        self._approval_requests[request_id] = (updated, capability)
        self._persist_request(updated)
        return updated

    def _persist_request(self, request: ToolApprovalRequest) -> None:
        if self._database_path is None:
            return
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("INSERT OR REPLACE INTO tool_approval_requests (id, tool, scope, session_id, arguments_fingerprint, summary, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM tool_approval_requests WHERE id = ?), ?), ?)", (request.id, request.tool, request.scope, request.session_id, request.arguments_fingerprint, request.summary, request.status, request.id, now, now))

    def _load_request(self, request_id: str, scope: str) -> ToolApprovalRequest:
        if self._database_path is None:
            raise KeyError(request_id)
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute("SELECT id, tool, scope, session_id, arguments_fingerprint, summary, status FROM tool_approval_requests WHERE id = ? AND scope = ?", (request_id, scope)).fetchone()
        if row is None:
            raise KeyError(request_id)
        return ToolApprovalRequest(*row)

    def get_approval_request(self, request_id: str, scope: str) -> ToolApprovalRequest:
        current = self._approval_requests.get(request_id)
        if current is not None:
            if current[0].scope != scope:
                raise KeyError(request_id)
            return current[0]
        return self._load_request(request_id, scope)

    def issue_approved_request(self, request_id: str, scope: str, authorization: ToolAuthorization, arguments: dict[str, Any], session_id: str | None) -> ToolApproval:
        request = self.get_approval_request(request_id, scope)
        tool = self.get(request.tool)
        fingerprint = hashlib.sha256(json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        if request.status != "approved" or not tool.high_impact or request.scope != scope or request.session_id != session_id or request.arguments_fingerprint != fingerprint or not self.validate_authorization(authorization, request.tool, scope, session_id):
            raise ValueError("approval request is not valid")
        consumed = ToolApproval(str(uuid4()), authorization.token, request.tool, scope, session_id, fingerprint)
        updated = ToolApprovalRequest(request.id, request.tool, request.scope, request.session_id, request.arguments_fingerprint, request.summary, "consumed")
        self._approval_requests[request.id] = (updated, None)
        self._persist_request(updated)
        self._approvals[consumed.token] = consumed
        return consumed

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
