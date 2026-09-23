from __future__ import annotations

from typing import Any

from app.plans.specs import ExecutionSpecService
from app.tools.registry import ToolApproval, ToolAuthorization, ToolRegistry


class TrustedExecutionBridge:
    def __init__(self, specs: ExecutionSpecService, tools: ToolRegistry) -> None:
        self._specs = specs
        self._tools = tools

    def prepare(self, *, scope: str, spec_id: str, approval_request_id: str | None = None) -> tuple[Any, ToolAuthorization, ToolApproval | None]:
        spec = self._specs.get(spec_id, scope)
        if spec is None or spec.status != "ready":
            raise ValueError("spec is not ready")
        tool = self._tools.get(spec.tool_name)
        authorization = self._tools.authorize(spec.tool_name, scope, spec.session_id)
        approval = None
        if tool.high_impact:
            if approval_request_id is None:
                raise ValueError("approval request required")
            approval = self._tools.issue_approved_request(approval_request_id, scope, authorization, spec.arguments, spec.session_id)
        runtime = self._specs.materialize(spec_id, scope, authorization, approval)
        return runtime, authorization, approval
