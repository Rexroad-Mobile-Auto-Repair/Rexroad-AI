from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class KnowledgeChunk(BaseModel):
    chunk_id: str
    content: str
    source_type: Literal["source_code"] = "source_code"
    workspace: str
    file_path: str
    language: str
    line_start: int
    line_end: int
    symbol_name: str | None = None
    symbol_type: str | None = None
    parent_symbol: str | None = None
    content_hash: str
    git_commit_sha: str | None = None
    indexed_at: datetime
    metadata: dict[str, str] = Field(default_factory=dict)


class Evidence(BaseModel):
    chunk_id: str
    workspace: str
    file_path: str
    line_start: int
    line_end: int
    symbol_name: str | None = None
    symbol_type: str | None = None
    content_hash: str
    git_commit_sha: str | None = None
    retrieval_method: Literal["lexical"] = "lexical"
    score: float
    rank: int
    freshness: Literal["current", "stale", "missing"]


class KnowledgeSearchResult(BaseModel):
    chunk: KnowledgeChunk
    evidence: Evidence


class KnowledgeIndexResult(BaseModel):
    workspace: str
    indexed_chunks: int
    indexed_files: int
    skipped_files: int
    git_commit_sha: str | None = None
