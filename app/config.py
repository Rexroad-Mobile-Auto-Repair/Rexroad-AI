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

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5-coder:14b"

    seo_crawler_workspace: str = r"D:\Rexroad-SEO-Crawler"
    knowledge_workspace: str = r"D:\Rexroad-AI-Knowledge"

    request_timeout_seconds: float = Field(default=120.0, gt=0)
