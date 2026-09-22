from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.diagnostics.models import ProviderCapabilityHealth
from app.diagnostics.service import (
    CapabilitySpec,
    ProviderDiagnostics,
    build_local_diagnostics,
)


class FakeProbe:
    def __init__(self, statuses: dict[str, str]) -> None:
        self.statuses = statuses

    async def probe(self, spec: CapabilitySpec) -> ProviderCapabilityHealth:
        status = self.statuses[spec.capability]
        return ProviderCapabilityHealth(
            capability=spec.capability,
            provider=spec.provider,
            provider_identity=spec.provider_identity,
            model=spec.model,
            endpoint=spec.endpoint,
            status=status,
            available=status == "available",
            failure_reason=None if status == "available" else "safe failure",
        )


def specs() -> list[CapabilitySpec]:
    return [
        CapabilitySpec("embeddings", "fake", "fake:http://local", "embed", "http://local/v1"),
        CapabilitySpec("chat", "fake", "fake:http://local", "chat", "http://local/v1"),
    ]


@pytest.mark.asyncio
async def test_diagnostics_reports_healthy_capabilities_in_deterministic_order() -> None:
    report = await ProviderDiagnostics(
        specs(), FakeProbe({"chat": "available", "embeddings": "available"})
    ).report()

    assert report.status == "healthy"
    assert [item.capability for item in report.capabilities] == ["chat", "embeddings"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["unreachable", "timeout", "provider_error", "model_unavailable"])
async def test_diagnostics_preserves_unhealthy_status_without_crashing(status: str) -> None:
    report = await ProviderDiagnostics(
        specs(), FakeProbe({"chat": status, "embeddings": "available"})
    ).report()

    assert report.status == "degraded"
    assert report.capabilities[0].status == status
    assert report.capabilities[1].available is True


@pytest.mark.asyncio
async def test_probe_failure_isolated_from_other_capabilities() -> None:
    class FailingProbe(FakeProbe):
        async def probe(self, spec: CapabilitySpec) -> ProviderCapabilityHealth:
            if spec.capability == "chat":
                raise RuntimeError("secret-token should never be returned")
            return await super().probe(spec)

    report = await ProviderDiagnostics(
        specs(), FailingProbe({"chat": "available", "embeddings": "available"})
    ).report()

    assert report.capabilities[0].status == "provider_error"
    assert report.capabilities[0].failure_reason == "Capability probe failed"
    assert "secret-token" not in report.model_dump_json()
    assert report.capabilities[1].available is True


@pytest.mark.asyncio
async def test_unexpected_probe_exception_isolated_without_leaking_message() -> None:
    class UnexpectedProbe(FakeProbe):
        async def probe(self, spec: CapabilitySpec) -> ProviderCapabilityHealth:
            if spec.capability == "chat":
                raise OSError("secret-token")
            return await super().probe(spec)

    report = await ProviderDiagnostics(
        specs(), UnexpectedProbe({"chat": "available", "embeddings": "available"})
    ).report()

    assert report.status == "degraded"
    assert report.capabilities[0].status == "provider_error"
    assert report.capabilities[0].failure_reason == "Capability probe failed"
    assert report.capabilities[1].available is True
    assert "secret-token" not in report.model_dump_json()


def test_local_diagnostics_sanitizes_endpoint_and_credentials() -> None:
    settings = SimpleNamespace(
        local_openai_base_url="HTTP://user:password@LOCALHOST:80/v1/?token=secret",
        local_embedding_model="embed-model",
        local_openai_model="chat-model",
        local_openai_api_key="api-secret",
    )

    diagnostics = build_local_diagnostics(settings)

    assert {spec.endpoint for spec in diagnostics._specs} == {"http://localhost/v1"}
    assert "password" not in diagnostics._specs[0].provider_identity
    assert "secret" not in str(diagnostics._specs)


@pytest.mark.asyncio
async def test_all_unavailable_report_is_unavailable() -> None:
    report = await ProviderDiagnostics(
        specs(), FakeProbe({"chat": "unreachable", "embeddings": "timeout"})
    ).report()

    assert report.status == "unavailable"


def test_doctor_endpoint_returns_structured_report(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDiagnostics:
        async def report(self):
            return {
                "status": "degraded",
                "capabilities": [
                    {
                        "capability": "chat",
                        "provider": "fake",
                        "provider_identity": "fake:http://local",
                        "model": "chat",
                        "endpoint": "http://local/v1",
                        "status": "available",
                        "available": True,
                        "latency_ms": 1.0,
                        "failure_reason": None,
                    }
                ],
            }

    monkeypatch.setattr(main_module, "diagnostics", FakeDiagnostics())
    response = TestClient(main_module.app).get("/system/doctor")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["capabilities"][0]["capability"] == "chat"
