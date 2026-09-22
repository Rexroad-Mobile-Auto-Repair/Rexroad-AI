from fastapi import FastAPI

app = FastAPI(
    title="Rexroad AI",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "rexroad-ai",
        "version": "0.1.0",
    }
