from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.diagnostics.models import DoctorReport, ProviderCapabilityHealth
from app.knowledge.embeddings import OpenAICompatibleEmbeddingProvider


@dataclass(frozen=True)
class CapabilitySpec:
    capability: str
    provider: str
    provider_identity: str
    model: str
    endpoint: str


class CapabilityProbe(Protocol):
    async def probe(self, spec: CapabilitySpec) -> ProviderCapabilityHealth: ...


class OpenAICompatibleCapabilityProbe:
    def __init__(self, api_key: str | None = None, timeout: float = 5.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    async def probe(self, spec: CapabilitySpec) -> ProviderCapabilityHealth:
        started = time.perf_counter()
        base_url = spec.endpoint
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                models_response = await client.get(f"{base_url}/models", headers=headers)
                models_response.raise_for_status()
                models = models_response.json().get("data", [])
                model_ids = {str(item.get("id")) for item in models if isinstance(item, dict)}
                if spec.model not in model_ids:
                    return self._result(spec, "model_unavailable", started, "Configured model was not listed by the provider")
                if spec.capability == "embeddings":
                    response = await client.post(
                        f"{base_url}/embeddings",
                        json={"model": spec.model, "input": ["health check"]},
                        headers=headers,
                    )
                    response.raise_for_status()
                    data = response.json().get("data", [])
                    if not data or not data[0].get("embedding"):
                        return self._result(spec, "provider_error", started, "Provider returned no embedding")
            return self._result(spec, "available", started)
        except httpx.TimeoutException:
            return self._result(spec, "timeout", started, "Provider probe timed out")
        except httpx.ConnectError:
            return self._result(spec, "unreachable", started, "Provider endpoint was unreachable")
        except (httpx.HTTPStatusError, httpx.RequestError, ValueError, KeyError, TypeError):
            return self._result(spec, "provider_error", started, "Provider probe failed")

    @staticmethod
    def _result(spec: CapabilitySpec, status: str, started: float, reason: str | None = None) -> ProviderCapabilityHealth:
        return ProviderCapabilityHealth(
            capability=spec.capability,
            provider=spec.provider,
            provider_identity=spec.provider_identity,
            model=spec.model,
            endpoint=spec.endpoint,
            status=status,
            available=status == "available",
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            failure_reason=reason,
        )


class ProviderDiagnostics:
    def __init__(self, specs: list[CapabilitySpec], probe: CapabilityProbe) -> None:
        self._specs = specs
        self._probe = probe

    async def report(self) -> DoctorReport:
        results: list[ProviderCapabilityHealth] = []
        for spec in sorted(self._specs, key=lambda item: item.capability):
            try:
                results.append(await self._probe.probe(spec))
            except Exception:  # noqa: BLE001 - fault-isolation boundary
                results.append(
                    ProviderCapabilityHealth(
                        capability=spec.capability,
                        provider=spec.provider,
                        provider_identity=spec.provider_identity,
                        model=spec.model,
                        endpoint=spec.endpoint,
                        status="provider_error",
                        available=False,
                        failure_reason="Capability probe failed",
                    )
                )
        available = [item.available for item in results]
        status = "healthy" if all(available) else "unavailable" if not any(available) else "degraded"
        return DoctorReport(status=status, capabilities=results)


def build_local_diagnostics(settings: Any) -> ProviderDiagnostics:
    embedding = OpenAICompatibleEmbeddingProvider(
        settings.local_openai_base_url,
        settings.local_embedding_model,
        settings.local_openai_api_key,
    )
    endpoint = embedding.endpoint_identity
    provider_identity = f"openai_compatible:{endpoint}"
    return ProviderDiagnostics(
        [
            CapabilitySpec("chat", "openai_compatible", provider_identity, settings.local_openai_model, endpoint),
            CapabilitySpec("embeddings", "openai_compatible", provider_identity, settings.local_embedding_model, endpoint),
        ],
        OpenAICompatibleCapabilityProbe(settings.local_openai_api_key),
    )
