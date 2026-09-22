from pydantic import BaseModel

from app.providers.models import ProviderName


class AgentQueryRequest(BaseModel):
    message: str
    provider: ProviderName | None = None
    model: str | None = None


class AgentQueryResponse(BaseModel):
    provider: ProviderName
    model: str
    content: str
