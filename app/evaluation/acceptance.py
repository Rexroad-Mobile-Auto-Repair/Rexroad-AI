from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import PurePosixPath

from app.config import Settings
from app.diagnostics.service import build_local_diagnostics
from app.evaluation.acceptance_models import (
    AcceptanceObservationCase,
    AcceptanceObservationResult,
)
from app.evaluation.models import RetrievalBenchmarkCase
from app.evaluation.runner import EvaluationRunner
from app.knowledge.embeddings import build_embedding_provider
from app.knowledge.service import KnowledgeService
from app.knowledge.store import KnowledgeStore
from app.policy.factory import build_workspace_registry
from app.policy.workspaces import WorkspaceAccessError


def _observable(results: list) -> list[tuple]:
    return [(item.chunk.chunk_id, item.evidence.file_path, item.evidence.line_start,
             item.evidence.line_end, item.evidence.symbol_name, item.evidence.symbol_type,
             item.evidence.freshness, item.evidence.retrieval_method, item.evidence.rank)
            for item in results]


def _unsafe_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return (not normalized or normalized.startswith("/") or
            (len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/") or
            any(part == ".." for part in PurePosixPath(normalized).parts))


def _safe_workspace(value: str) -> bool:
    return bool(value.strip()) and "\r" not in value and "\n" not in value and not _unsafe_path(value)


def _observation(knowledge: KnowledgeService, case: AcceptanceObservationCase, mode: str = "lexical") -> AcceptanceObservationResult:
    started = time.perf_counter()
    try:
        first = knowledge.search(case.workspace, case.query, case.top_k, mode)
        second = knowledge.search(case.workspace, case.query, case.top_k, mode)
    except Exception:  # noqa: BLE001 - safe local acceptance boundary
        return AcceptanceObservationResult(case_id=case.case_id, workspace=case.workspace,
            target_file_path=case.target_file_path, status="error", safe_reason="retrieval_error")
    if any(_unsafe_path(item.evidence.file_path) for item in [*first, *second]):
        return AcceptanceObservationResult(case_id=case.case_id, workspace=case.workspace,
            target_file_path=case.target_file_path, status="error", safe_reason="unsafe_result_path")
    match = next((item for item in first
                  if item.evidence.file_path == case.target_file_path and
                  (case.target_symbol_name is None or item.evidence.symbol_name == case.target_symbol_name)), None)
    status = "observed"
    reason = "target_not_in_top_k" if match is None else None
    if match is not None and match.evidence.freshness != "current":
        status, reason = "prerequisite_unavailable", "target_not_current"
    repeatable = _observable(first) == _observable(second)
    outcome = AcceptanceObservationResult(case_id=case.case_id, workspace=case.workspace,
        target_file_path=case.target_file_path, target_symbol_name=case.target_symbol_name,
        mode=mode, status=status, observed_rank=match.evidence.rank if match else None,
        matched_file_path=match.evidence.file_path if match else None,
        matched_symbol_name=match.evidence.symbol_name if match else None,
        freshness=match.evidence.freshness if match else None,
        repeatable=repeatable,
        duration_ms=round((time.perf_counter() - started) * 1000, 3), safe_reason=reason,
        ordered_result_ids=[item.chunk.chunk_id for item in first])
    if not repeatable:
        outcome.status = "error"
        outcome.safe_reason = "repeatability_mismatch"
    return outcome


def run_acceptance(settings: Settings, workspace: str = "seo_crawler", mode: str = "lexical") -> dict:
    registry = build_workspace_registry(settings)
    try:
        registry.get_root(workspace)
    except WorkspaceAccessError:
        return {"workspace": workspace, "assertions": [], "observations": [],
                "status": "prerequisite_unavailable", "safe_reason": "workspace_unavailable"}
    index_path = __import__("pathlib").Path(settings.knowledge_index_path)
    if not index_path.exists():
        return {"workspace": workspace, "assertions": [], "observations": [],
                "status": "prerequisite_unavailable", "safe_reason": "index_unavailable"}
    store = KnowledgeStore(index_path)
    if not store.list_workspace(workspace):
        return {"workspace": workspace, "assertions": [], "observations": [],
                "status": "prerequisite_unavailable", "safe_reason": "index_empty"}
    assertion = RetrievalBenchmarkCase(case_id="exact-analysis-runs", workspace=workspace,
        query="list_analysis_runs_for_crawl", mode="lexical", top_k=10,
        expected_file_path="app/storage/database.py", expected_symbol_name="list_analysis_runs_for_crawl",
        max_rank=1)
    observation = AcceptanceObservationCase(case_id="indexability-concept", workspace=workspace,
        query="how does Rexroad determine whether a crawled page can appear in Google search results",
        top_k=10, target_file_path="app/crawler/page_persistence.py", target_symbol_name="derive_indexability")
    if mode not in {"lexical", "semantic", "hybrid", "all"}:
        return {"workspace": workspace, "assertions": [], "observations": [],
                "status": "prerequisite_unavailable", "safe_reason": "invalid_mode"}
    embedding = None
    capability = None
    if mode in {"semantic", "hybrid", "all"}:
        try:
            report = asyncio.run(build_local_diagnostics(settings).report())
            capability = next(item for item in report.capabilities if item.capability == "embeddings")
        except Exception:  # noqa: BLE001 - safe acceptance boundary
            return {"workspace": workspace, "assertions": [], "observations": [],
                    "status": "error", "safe_reason": "capability_probe_error"}
        if capability.available:
            try:
                embedding = build_embedding_provider(settings)
            except Exception:  # noqa: BLE001 - safe acceptance boundary
                return {"workspace": workspace, "assertions": [], "observations": [],
                        "status": "error", "safe_reason": "capability_probe_error"}
    knowledge = KnowledgeService(registry, store, embedding)
    assertion_result = EvaluationRunner(knowledge).run_case(assertion)
    assertion_payload = assertion_result.model_dump()
    if assertion_result.status == "passed" and assertion_result.observed_freshness != "current":
        assertion_payload["status"] = "prerequisite_unavailable"
        assertion_payload["reason"] = "target_not_current"
    observation_result = _observation(knowledge, observation)
    observations = [observation_result.model_dump()] if mode in {"lexical", "all"} else []
    requested_modes = [mode] if mode != "all" else ["semantic", "hybrid"]
    for requested in requested_modes:
        if requested == "lexical":
            continue
        if capability is not None and capability.available:
            observations.append(_observation(knowledge, observation, requested).model_dump())
        else:
            observations.append(AcceptanceObservationResult(case_id=observation.case_id,
                workspace=workspace, mode=requested, status="skipped",
                target_file_path=observation.target_file_path,
                target_symbol_name=observation.target_symbol_name,
                safe_reason="capability_unavailable",
                capability_status=capability.status if capability is not None else "unavailable").model_dump())
    return {"workspace": workspace, "assertions": [assertion_payload],
            "observations": observations, "status": "ok"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="seo_crawler")
    parser.add_argument("--mode", choices=["lexical", "semantic", "hybrid", "all"], default="lexical")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not _safe_workspace(args.workspace):
        report = {"workspace": "", "assertions": [], "observations": [],
                  "status": "prerequisite_unavailable", "safe_reason": "invalid_workspace"}
        if args.json:
            print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        else:
            print("Rexroad Retrieval Acceptance\nPREREQUISITE_UNAVAILABLE\n  reason: invalid_workspace")
        return 1
    report = (run_acceptance(Settings(), args.workspace)
              if args.mode == "lexical" else run_acceptance(Settings(), args.workspace, args.mode))
    if args.json:
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    else:
        print(f"Rexroad Retrieval Acceptance\nworkspace: {report['workspace']}")
        if report.get("status") != "ok":
            print("PREREQUISITE_UNAVAILABLE\n  reason: " + str(report.get("safe_reason")))
        for item in report.get("assertions", []):
            print(f"{item['status'].upper()}  {item.get('case_id', 'assertion')}")
        for item in report.get("observations", []):
            label = "SKIP" if item.get("status") == "skipped" else "OBSERVE"
            detail = f"rank {item.get('observed_rank')}" if item.get("observed_rank") else str(item.get("safe_reason"))
            print(f"{label} {item.get('case_id', 'observation')}\n  {item.get('mode', 'lexical')}: {detail}")
    assertions_ok = all(item["status"] == "passed" for item in report.get("assertions", []))
    observations_ok = all(item["status"] in {"observed", "prerequisite_unavailable", "skipped"}
                          for item in report.get("observations", []))
    return 0 if report.get("status") == "ok" and assertions_ok and observations_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
