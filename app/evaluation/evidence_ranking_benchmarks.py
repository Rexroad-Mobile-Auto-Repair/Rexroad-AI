from __future__ import annotations

from app.evaluation.evidence_ranking_acceptance import EvidenceRankingCase

EVIDENCE_RANKING_BENCHMARK_SUITE_ID = "evidence-ranking-m1-authoritative-v1"

EVIDENCE_RANKING_BENCHMARKS = (
    EvidenceRankingCase(
        case_id="exact-analysis-runs",
        workspace="seo_crawler",
        query="list_analysis_runs_for_crawl",
        target_file_path="app/storage/database.py",
        target_symbol_name="list_analysis_runs_for_crawl",
    ),
    EvidenceRankingCase(
        case_id="indexability-concept",
        workspace="seo_crawler",
        query="how does Rexroad determine whether a crawled page can appear in Google search results",
        target_file_path="app/crawler/page_persistence.py",
        target_symbol_name="derive_indexability",
    ),
)
