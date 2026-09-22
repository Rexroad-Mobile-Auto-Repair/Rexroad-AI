from typing import Literal

from pydantic import BaseModel, Field

ProviderName = Literal["openai", "anthropic", "ollama"]


class ModelRequest(BaseModel):
    model: str
    messages: list[dict[str, str]]
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)


class ModelResponse(BaseModel):
    provider: ProviderName
    model: str
    content: str
