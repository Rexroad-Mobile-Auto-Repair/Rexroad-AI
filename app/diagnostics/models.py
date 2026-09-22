from typing import Literal

from pydantic import BaseModel

CapabilityStatus = Literal[
    "available",
    "unreachable",
    "timeout",
    "provider_error",
    "model_unavailable",
    "misconfigured",
]


class ProviderCapabilityHealth(BaseModel):
    capability: str
    provider: str
    provider_identity: str
    model: str
    endpoint: str
    status: CapabilityStatus
    available: bool
    latency_ms: float | None = None
    failure_reason: str | None = None


class DoctorReport(BaseModel):
    status: Literal["healthy", "degraded", "unavailable"]
    capabilities: list[ProviderCapabilityHealth]
