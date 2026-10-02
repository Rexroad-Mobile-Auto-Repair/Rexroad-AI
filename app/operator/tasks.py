import json
import re

from app.agents.models import AgentQueryRequest
from app.skills.service import SkillService

SHORTCUTS = {
    "review-code": "Review code",
    "review-tests": "Review tests",
    "investigate-error": "Investigate an error",
    "review-security": "Review security",
}


def shortcut_warnings(content):
    claims = re.search(
        r"(?:^|[.!?]\s*|\n)\s*(?:(?:all|the)\s+)?(?:tests?|test suite|pytest)\s+(?:pass|passed|fail|failed)\b",
        content,
        re.IGNORECASE,
    )
    executed = re.search(
        r"(?:^|[.!?]\s*|\n)\s*(?:I|we)\s+(?:ran|executed)\s+(?:the\s+)?(?:tests|pytest)\b",
        content,
        re.IGNORECASE,
    )
    return (
        [
            "This review claims a test result, but the read-only shortcut did not run checks. Verify with a saved check before relying on that claim."
        ]
        if claims or executed
        else []
    )


def shortcut_request(workspace, target, shortcut, skills: SkillService, search, note=""):
    if shortcut not in SHORTCUTS:
        raise ValueError("Unknown task shortcut")
    # Reuse the source viewer policy; a shortcut cannot select private/outside files.
    search.source(workspace, target)
    skill = skills.get(shortcut, search.workspaces.get_root(workspace))
    if skill is None or not skill.user_invocable or not skill.allowed_tools:
        raise ValueError("Shortcut skill is unavailable")
    if not set(skill.allowed_tools) <= {
        "filesystem.read",
        "filesystem.grep",
        "filesystem.glob",
        "workspace.symbols",
    }:
        raise ValueError("Shortcut must use read-only source tools")
    SkillService.render(skill, {"target": target, "note": note})
    return AgentQueryRequest(
        workspace=workspace,
        message=f"/skill {shortcut} "
        + json.dumps({"target": target, "note": note}, ensure_ascii=False),
    )


def project_progress(scope, workspace, workflows, jobs, workers, plans, workspaces):
    workspaces.get_root(workspace)
    if not scope.strip() or len(scope) > 200:
        raise ValueError("Choose a scope of up to 200 characters")
    result = []
    for workflow in workflows.list(scope, workspace, 20):
        job = jobs.get(workflow.workflow_id, scope)
        if job is None:
            continue
        worker_records = []
        for role, task_id in [
            ("Analysis", job.analyst_task_id),
            ("Verification", job.verifier_task_id),
        ]:
            record = workers.get(task_id) if task_id else None
            if record and record[0].scope == scope and record[0].workspace == workspace:
                task, report = record
                worker_records.append(
                    {
                        "role": role,
                        "task_id": task_id,
                        "status": report.status if report else task.status,
                        "reason": report.safe_reason if report else None,
                    }
                )
        result.append(
            {
                "workflow_id": job.workflow_id,
                "objective": job.objective[:500],
                "status": job.status,
                "next_action": job.next_action.action,
                "blocked_reason": job.blocked_reason,
                "workers": worker_records,
                "proposal_status": job.proposal_status,
                "files": [
                    {"path": s.relative_path, "status": s.execution_status} for s in job.patch_specs
                ],
                "checks": [
                    {"check": s.check_id, "status": s.execution_status} for s in job.check_specs
                ],
            }
        )
    saved_plans = [
        {
            "plan_id": p.id,
            "status": p.status,
            "steps": [{"title": s.title, "status": s.status} for s in p.steps],
        }
        for p in plans.list(scope, 20)
        if p.workspace == workspace
    ]
    return {"workspace": workspace, "scope": scope, "workflows": result, "plans": saved_plans}
