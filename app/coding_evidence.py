from __future__ import annotations

import ast
import hashlib

from app.coding_contracts import claims_failed_checks


def verification_conflicts(workflow, workspaces, specs, traces, agents) -> list[str]:
    """Compare a pending verifier with executor evidence and current approved files."""
    conflicts = []
    checks_passed = bool(workflow.check_spec_ids) and len(workflow.check_spec_ids) == len(workflow.check_trace_ids)
    for spec_id, trace_id in zip(workflow.check_spec_ids, workflow.check_trace_ids):
        spec, trace = specs.get(spec_id, workflow.scope), traces.get(trace_id, workflow.scope)
        try:
            result = ast.literal_eval(trace.result_preview or "") if trace else {}
        except (SyntaxError, ValueError):
            result = {}
        if not spec or not trace or trace.tool != "workspace.run_check" or trace.plan_id != spec.plan_id or trace.step_id != spec.step_id or trace.status != "success" or not isinstance(result, dict) or result.get("passed") is not True:
            checks_passed = False
    if not checks_passed:
        conflicts.append("Successful saved checks are missing or do not match this workflow.")
    current_sources = {}
    for spec_id, trace_id in zip(workflow.mutation_spec_ids, workflow.mutation_trace_ids):
        spec, trace = specs.get(spec_id, workflow.scope), traces.get(trace_id, workflow.scope)
        try:
            result = ast.literal_eval(trace.result_preview or "") if trace else {}
            path = spec.arguments["relative_path"]
            file = workspaces.resolve_path(workflow.workspace, path)
            text = file.read_text(encoding="utf-8")
            # apply_patch hashes UTF-8 text after newline normalization, not raw bytes.
            if trace.status != "success" or trace.tool != "filesystem.apply_patch" or trace.plan_id != spec.plan_id or trace.step_id != spec.step_id or hashlib.sha256(text.encode()).hexdigest() != result.get("after_hash"):
                raise ValueError("stale source")
            current_sources[path] = text
        except (AttributeError, KeyError, OSError, SyntaxError, ValueError):
            conflicts.append("An approved file no longer matches its successful patch evidence.")
    if len(current_sources) != len(workflow.mutation_spec_ids) or not current_sources:
        conflicts.append("Current approved file evidence is incomplete.")
    record = agents.get(workflow.verifier_task_id) if workflow.verifier_task_id else None
    if not record or record[0].scope != workflow.scope or record[0].workspace != workflow.workspace or not record[1] or record[1].status != "completed":
        conflicts.append("A completed verifier result for this workspace and scope is required.")
    else:
        if checks_passed and claims_failed_checks(record[1].summary):
            conflicts.append("The verifier claims failed checks, but the saved executor checks passed. Review the conflict before acceptance.")
        if getattr(agents, "provider_backed", False):
            sources = {item["path"]: item for item in agents.source_evidence(workflow.scope, workflow.verifier_task_id)}
            for path, text in current_sources.items():
                evidence = sources.get(path)
                if not evidence or evidence["truncated"] or evidence["text"] != text:
                    conflicts.append(f"The verifier lacks complete current source evidence for {path}.")
    return list(dict.fromkeys(conflicts))
