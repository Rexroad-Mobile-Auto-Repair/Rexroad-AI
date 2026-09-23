from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    default_provider: str = "openai_compatible"

    openai_api_key: str | None = None
    openai_model: str = "gpt-5.6"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-5"

    local_openai_base_url: str = "http://localhost:1234/v1"
    local_openai_api_key: str | None = None
    local_openai_model: str = "qwen3-coder-30b-a3b-instruct"
    local_embedding_model: str = "text-embedding-nomic-embed-text-v1.5"

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5-coder:14b"

    seo_crawler_workspace: str = r"D:\Rexroad-SEO-Crawler"
    knowledge_workspace: str = r"D:\Rexroad-AI-Knowledge"

    action_journal_path: str = "data/action_journal.sqlite3"
    knowledge_index_path: str = "data/knowledge_index.sqlite3"

    request_timeout_seconds: float = Field(default=120.0, gt=0)

    model_context_byte_budget: int = Field(default=65536, gt=0)
    model_tool_result_byte_budget: int = Field(default=16384, gt=0)

    model_max_evidence_items: int = Field(default=8, gt=0)
    model_total_evidence_byte_budget: int = Field(default=24576, gt=0)
    model_evidence_content_byte_budget: int = Field(default=8192, gt=0)
    knowledge_max_source_bytes: int = Field(default=5_000_000, gt=0)
    knowledge_max_extracted_bytes: int = Field(default=5_000_000, gt=0)
    knowledge_max_extracted_sections: int = Field(default=1000, gt=0)
    knowledge_max_document_chunks: int = Field(default=1000, gt=0)

