"""Session-scoped chat controls over Rexroad's existing durable plan services.

The task/owner/dependency interface is informed by Clawd-Code's task tools;
Rexroad's SQLite plans and scoped execution remain authoritative.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable

from app.agents.context import read_only_intent
from app.autonomy.service import AutonomousContinuationService
from app.autonomy.team import AutonomousTeamCoordinator
from app.plans.models import ProjectPlan
from app.plans.service import PlanService


class ChatPlannedReview:
    """Compatibility name for general saved-plan controls, including old reviews."""

    def __init__(self, plans: PlanService, autonomy: AutonomousContinuationService,
                 teams: AutonomousTeamCoordinator, resume: Callable[[str, str], dict],
                 validate_workspace: Callable,
                 create_plan: Callable[[str, str, str, bool], Awaitable[ProjectPlan]] | None = None,
                 workflow_records: Callable[[ProjectPlan], list[dict]] | None = None,
                 worker_records: Callable[[ProjectPlan], list[dict]] | None = None,
                 cancel_plan: Callable[[ProjectPlan], None] | None = None) -> None:
        self._plans, self._autonomy, self._teams = plans, autonomy, teams
        self._resume, self._validate_workspace = resume, validate_workspace
        self._create_plan, self._workflow_records = create_plan, workflow_records
        self._worker_records = worker_records
        self._cancel_plan = cancel_plan
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def requested(message: str) -> bool:
        text = message.lower()
        return bool(re.search(r"^\s*(?:(?:please|can you|could you|let's)\s+)?(create|start|make|build)\b.*\bplan\b", text)
                    and not re.search(r"\b(do not|don't|never)[^.\n]*\b(create|start|make|build)\b", text))

    @classmethod
    def _command(cls, message: str) -> tuple[str, str] | None:
        text = message.strip()
        if text.startswith("/plan"):
            parts = text.split(maxsplit=2)
            operation = parts[1].lower() if len(parts) > 1 else "help"
            return operation, parts[2] if len(parts) > 2 else ""
        lower = text.lower()
        if re.match(r"^(?:start|run|execute)\s+(?:the\s+|saved\s+)?plan\b", lower):
            return "start", text
        if cls.requested(text):
            return "create-run" if re.search(r"\b(run|execute|combine)\b", lower) and not re.search(r"\b(do not|don't|never)\s+(run|execute|combine)\b", lower) else "create", text
        if re.search(r"\b(cancel|stop)\b.*\b(plan|workflow)\b", lower) and not re.search(r"\b(do not|don't|never)\s+(cancel|stop)\b", lower):
            return "cancel", text
        if "saved findings" in lower or "their findings" in lower:
            return "findings", text
        if re.search(r"\b(list|show)\b.*\bplans\b", lower):
            return "list", ""
        if re.search(r"\b(continue|resume)\b", lower) and not re.search(r"\b(do not|don't|never)\s+(continue|resume)\b", lower):
            return "continue", text
        if any(term in lower for term in ("persisted records", "saved state", "current status", "plan id", "team id")):
            return "status", text
        return None

    async def handle(self, message: str, workspace: str | None, session_id: str) -> str | None:
        command = self._command(message)
        if command is None:
            return None
        operation, argument = command
        if operation not in {"create", "create-run", "start", "continue", "status", "findings", "list", "cancel", "reconcile"}:
            return "Saved-plan controls: /plan create <goal>, /plan start [plan ID], /plan status [plan ID], /plan findings [plan ID], /plan list, /plan cancel [plan ID], /plan reconcile [plan ID]. Creation saves a plan; start runs authorized work. Reconcile compares saved evidence without rerunning workers. File changes still require the supervised approval workflow."
        scope = "chat:" + session_id
        create = operation in {"create", "create-run"}
        if create and not argument.strip():
            return "Give a goal after /plan create."
        if message.startswith("/plan") and not create and operation != "list" and argument and not re.fullmatch(r"[0-9a-fA-F-]{36}", argument):
            return "Use a valid saved plan ID, or omit it to select the latest plan in this chat."
        if not workspace:
            return "Select a project workspace before starting a saved review." if create else "Select a project workspace to inspect saved plans."
        self._validate_workspace(workspace)
        if operation in {"status", "findings", "list"}:
            # Status remains available while another request is executing.
            return self._read(operation, argument, workspace, scope, message.startswith("/plan"))
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            if create:
                goal = " ".join(argument.split())[:4000]
                plan = next((p for p in reversed(self._plans.list(scope, 100)) if p.goal == goal and p.workspace == workspace), None)
                if plan is None:
                    if self._create_plan is None:
                        return "Saved-plan creation is not configured. No plan was created."
                    readonly = read_only_intent(argument) or bool(re.search(r"read[- ]only", argument, re.IGNORECASE))
                    try:
                        plan = await self._create_plan(goal, scope, workspace, readonly)
                    except ValueError:
                        return "The planner could not produce a valid scoped plan. No new plan was saved. Try a more specific goal with the desired analyst roles."
            else:
                plan = self._select(argument, workspace, scope)
            if plan is None:
                return "No saved plan was found in this chat. No work was started."
            if operation == "reconcile":
                for team in self._teams.list_for_plan(plan.id, scope):
                    if team['status'] in {'completed', 'failed'}:
                        await asyncio.to_thread(self._teams.reconcile_saved, team['team_id'], scope)
                return self._format(plan, findings=True)
            if operation == "cancel":
                if plan.status == "active":
                    if self._cancel_plan:
                        await asyncio.to_thread(self._cancel_plan, plan)
                    else:
                        self._autonomy.cancel(plan.id, scope)
                return self._format(plan, findings=False)
            if operation in {"create-run", "start", "continue"} and plan.status == "active":
                try:
                    await asyncio.to_thread(self._resume, plan.id, scope)
                except ValueError as exc:
                    return f"Resume stopped: {str(exc)[:300]}\n\n" + self._format(plan, findings=False)
            return self._format(plan, findings=operation in {"create-run", "start"})

    def _select(self, argument: str, workspace: str, scope: str) -> ProjectPlan | None:
        ids = re.findall(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", argument, re.IGNORECASE)
        if len(ids) > 1:
            return None
        plan = self._plans.get(ids[0].lower(), scope) if ids else next(iter(reversed(self._plans.list(scope, 100))), None)
        if plan is not None and plan.workspace != workspace:
            raise ValueError("Saved review workspace mismatch")
        return plan

    def _read(self, operation: str, argument: str, workspace: str, scope: str, explicit: bool) -> str | None:
        if operation == "list":
            plans = [p for p in self._plans.list(scope, 100) if p.workspace == workspace]
            return "\n\n".join(f"Plan ID: {p.id}\nStatus: {p.status}\nGoal: {p.goal}" for p in plans) or "No saved plans exist in this chat."
        plan = self._select(argument, workspace, scope)
        if plan is None:
            if explicit or "persisted records" in argument.lower():
                return "No saved multi-agent review exists in this chat. Earlier chat analysis is not a saved workflow."
            return None
        return self._format(plan, findings=operation == "findings")

    def _format(self, plan: ProjectPlan, *, findings: bool) -> str:
        state = self._autonomy.inspect(plan.id, plan.scope)
        team = self._teams.get_for_plan(plan.id, plan.scope)
        teams = self._teams.list_for_plan(plan.id, plan.scope)
        mutations = any(step.metadata.get("mutation_required") or step.metadata.get("worker") == "supervised_coding" for step in plan.steps)
        lines = [f"Workspace: {plan.workspace}", f"Plan ID: {plan.id}", f"Scope: {plan.scope}",
                 f"Current status: {state['plan_status']}", f"Team ID: {team['team_id'] if team else 'not created'}"]
        records = self._workflow_records(plan) if self._workflow_records else []
        if records:
            for record in records:
                lines.append(f"Coding workflow ID: {record['workflow_id']}\nProposal ID: {record.get('proposal_id') or 'none'}\nApproval/workflow status: {record['status']}")
        else:
            lines.extend(["Proposal ID: none", "Approval status: required before file changes; none granted by this chat control." if mutations else "Approval status: not required for this read-only review."])
        for step in state["steps"]:
            lines.append(f"Step {step['id']}: {step['title']} — {step['status']}" + (f"\nExecution reference: {step['reference']}" if step['reference'] else ""))
        workers = self._worker_records(plan) if self._worker_records else [result for saved_team in teams for result in saved_team["metadata"].get("worker_results", [])]
        for result in workers:
            lines.append(f"Worker task ID: {result['task_id']} ({result['worker_type']}, {result['status']})")
            if findings:
                lines.append("Files inspected: " + ", ".join(result.get("files_examined", [])))
                lines.append("Evidence references: " + ", ".join(result.get("evidence_refs", [])))
                if not teams:
                    lines.append(result.get("summary", ""))
        for saved_team in teams:
            lines.append(f"Team ID: {saved_team['team_id']}\nTeam status: {saved_team['status']}")
            synthesis = saved_team["metadata"].get("synthesis", {})
            lines.append("Reconciliation status: " + synthesis.get("reconciliation_status", "not performed for this older record"))
            if findings:
                lines.append("Combined saved findings:")
                lines.extend(synthesis.get("findings", []))
                for key in ("agreements", "disagreements", "unresolved_gaps"):
                    lines.append(key.replace("_", " ").capitalize() + ":")
                    lines.extend(synthesis.get(key, []) or ["None recorded."])
        if any(saved_team["metadata"].get("synthesis", {}).get("requires_more_work") for saved_team in teams):
            next_action = "Review unresolved or conflicting findings before implementation."
        elif state["plan_status"] == "completed":
            next_action = "Review the saved findings; no additional analysis steps are pending."
        elif state["plan_status"] in {"cancelled", "failed"}:
            next_action = "Inspect the saved outcome; this terminal plan will not rerun."
        else:
            next_action = "Start or continue authorized pending work, or review its linked supervised workflow."
        lines.append("Next required action: " + next_action)
        return "\n\n".join(lines)
