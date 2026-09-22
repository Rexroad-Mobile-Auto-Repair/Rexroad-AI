from fastapi import FastAPI

from app.config import Settings
from app.providers.factory import build_provider_registry, get_default_model
from app.providers.status import ProviderStatus

app = FastAPI(
    title="Rexroad AI",
    version="0.1.0",
)

settings = Settings()
provider_registry = build_provider_registry(settings)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "rexroad-ai",
        "version": "0.1.0",
    }


@app.get("/providers")
async def providers() -> list[ProviderStatus]:
    results: list[ProviderStatus] = []

    for name in provider_registry.names():
        provider = provider_registry.get(name)

        results.append(
            ProviderStatus(
                name=name,
                configured=True,
                healthy=await provider.health_check(),
                model=get_default_model(settings, name),
            )
        )

    return results
