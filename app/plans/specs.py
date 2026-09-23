from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from app.plans.models import PlanStep
from app.plans.service import PlanService
from app.plans.verification import (
    FieldEqualsPolicy,
    ResultPresentPolicy,
    TextContainsPolicy,
    VerificationPolicy,
)
from app.tools.registry import ToolRegistry

SpecStatus = Literal["draft", "ready", "invalidated"]


@dataclass(frozen=True)
class ExecutionSpec:
    id: str
    scope: str
    plan_id: str
    step_id: str
    tool_name: str
    arguments: dict[str, Any]
    verification: dict[str, Any]
    session_id: str | None
    status: SpecStatus
    created_at: str
    updated_at: str


class ExecutionSpecService:
    def __init__(self, database_path: str | Path, plans: PlanService, tools: ToolRegistry) -> None:
        self._database_path = Path(database_path)
        self._plans = plans
        self._tools = tools
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS execution_specs (id TEXT PRIMARY KEY, scope TEXT NOT NULL, plan_id TEXT NOT NULL, step_id TEXT NOT NULL, tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL, verification_json TEXT NOT NULL, session_id TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")

    def create(self, *, scope: str, plan_id: str, step_id: str, tool_name: str, arguments: dict[str, Any], verification: dict[str, Any], session_id: str | None = None) -> ExecutionSpec:
        step = self._actionable_step(scope, plan_id, step_id)
        if step is None:
            raise ValueError("step is not actionable")
        tool = self._tools.get(tool_name)
        self._validate_arguments(tool.parameters, arguments)
        now = datetime.now(UTC).isoformat()
        spec = ExecutionSpec(str(uuid4()), scope, plan_id, step_id, tool_name, arguments, verification, session_id, "draft", now, now)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("INSERT INTO execution_specs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (spec.id, spec.scope, spec.plan_id, spec.step_id, spec.tool_name, json.dumps(spec.arguments, sort_keys=True), json.dumps(spec.verification, sort_keys=True), spec.session_id, spec.status, spec.created_at, spec.updated_at))
        return spec

    def create_with_connection(self, connection: sqlite3.Connection, *, scope: str, plan_id: str, step_id: str, tool_name: str, arguments: dict[str, Any], verification: dict[str, Any], session_id: str | None = None, validate_step: bool = True) -> ExecutionSpec:
        if validate_step and self._actionable_step(scope, plan_id, step_id) is None:
            raise ValueError("step is not actionable")
        tool = self._tools.get(tool_name)
        self._validate_arguments(tool.parameters, arguments)
        now = datetime.now(UTC).isoformat()
        spec = ExecutionSpec(str(uuid4()), scope, plan_id, step_id, tool_name, arguments, verification, session_id, "draft", now, now)
        connection.execute("INSERT INTO execution_specs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (spec.id, spec.scope, spec.plan_id, spec.step_id, spec.tool_name, json.dumps(spec.arguments, sort_keys=True), json.dumps(spec.verification, sort_keys=True), spec.session_id, spec.status, spec.created_at, spec.updated_at))
        return spec

    def get(self, spec_id: str, scope: str) -> ExecutionSpec | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute("SELECT * FROM execution_specs WHERE id = ? AND scope = ?", (spec_id, scope)).fetchone()
        return self._from_row(row) if row else None

    def list(self, scope: str, limit: int = 50) -> list[ExecutionSpec]:
        if not scope.strip() or limit < 1 or limit > 100:
            raise ValueError("invalid scope or limit")
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute("SELECT * FROM execution_specs WHERE scope = ? ORDER BY created_at, id LIMIT ?", (scope, limit)).fetchall()
        return [self._from_row(row) for row in rows]

    def mark_ready(self, spec_id: str, scope: str) -> ExecutionSpec:
        spec = self.get(spec_id, scope)
        if spec is None:
            raise KeyError(spec_id)
        if self._actionable_step(scope, spec.plan_id, spec.step_id) is None:
            raise ValueError("spec is stale")
        if spec.status == "invalidated":
            raise ValueError("spec is invalidated")
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("UPDATE execution_specs SET status = ?, updated_at = ? WHERE id = ? AND scope = ?", ("ready", now, spec_id, scope))
        return self.get(spec_id, scope)  # type: ignore[return-value]

    def mark_ready_many(self, spec_ids: list[str], scope: str) -> list[ExecutionSpec]:
        if not spec_ids or len(spec_ids) != len(set(spec_ids)):
            raise ValueError("invalid spec set")
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = []
            for spec_id in spec_ids:
                row = connection.execute("SELECT * FROM execution_specs WHERE id=? AND scope=?", (spec_id, scope)).fetchone()
                if row is None or row[8] != "draft":
                    raise ValueError("spec is not draft")
                if self._actionable_step(scope, row[2], row[3]) is None:
                    raise ValueError("spec is stale")
                rows.append(row)
            now = datetime.now(UTC).isoformat()
            for spec_id in spec_ids:
                connection.execute("UPDATE execution_specs SET status='ready', updated_at=? WHERE id=? AND scope=? AND status='draft'", (now, spec_id, scope))
            updated = []
            for spec_id in spec_ids:
                row = connection.execute("SELECT * FROM execution_specs WHERE id=? AND scope=?", (spec_id, scope)).fetchone()
                updated.append(self._from_row(row))
            return updated

    def invalidate(self, spec_id: str, scope: str) -> ExecutionSpec:
        spec = self.get(spec_id, scope)
        if spec is None:
            raise KeyError(spec_id)
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("UPDATE execution_specs SET status = ?, updated_at = ? WHERE id = ? AND scope = ?", ("invalidated", now, spec_id, scope))
        return self.get(spec_id, scope)  # type: ignore[return-value]

    def materialize(self, spec_id: str, scope: str, authorization: Any, approval: Any = None) -> Any:
        spec = self.get(spec_id, scope)
        if spec is None or spec.status != "ready":
            raise ValueError("spec is not ready")
        if self._actionable_step(scope, spec.plan_id, spec.step_id) is None:
            raise ValueError("spec is stale")
        tool = self._tools.get(spec.tool_name)
        self._validate_arguments(tool.parameters, spec.arguments)
        policy = self._policy(spec.verification)
        from app.plans.continuation import StepExecutionSpec
        return StepExecutionSpec(spec.tool_name, authorization, spec.arguments, approval, policy, spec.session_id)

    @staticmethod
    def _policy(config: dict[str, Any]) -> VerificationPolicy | None:
        kind = config.get("type")
        if kind == "result_present" and len(config) == 1:
            return ResultPresentPolicy()
        if kind == "field_equals" and set(config) == {"type", "field", "expected"}:
            return FieldEqualsPolicy(config["field"], config["expected"])
        if kind == "text_contains" and set(config) == {"type", "text"}:
            return TextContainsPolicy(config["text"])
        raise ValueError("invalid verification policy")

    def _actionable_step(self, scope: str, plan_id: str, step_id: str) -> PlanStep | None:
        plan = self._plans.get(plan_id, scope)
        if plan is None or plan.status != "active":
            return None
        step = next((item for item in plan.steps if item.id == step_id), None)
        return step if step and step.status == "pending" and self._plans.next_step(plan_id, scope).id == step_id else None

    @staticmethod
    def _validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if any(name not in arguments for name in required) or (schema.get("additionalProperties") is False and any(name not in properties for name in arguments)):
            raise ValueError("invalid tool arguments")

    @staticmethod
    def _from_row(row: tuple[Any, ...]) -> ExecutionSpec:
        return ExecutionSpec(row[0], row[1], row[2], row[3], row[4], json.loads(row[5]), json.loads(row[6]), row[7], row[8], row[9], row[10])
