from pydantic import BaseModel

from app.providers.models import ProviderName


class ProviderStatus(BaseModel):
    name: ProviderName
    configured: bool
    healthy: bool
    model: str
