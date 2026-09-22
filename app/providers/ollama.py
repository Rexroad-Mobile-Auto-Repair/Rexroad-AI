import httpx

from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse


class OllamaProvider(ModelProvider):
    name = "ollama"

    def __init__(self, base_url: str = "http://127.0.0.1:11434") -> None:
        self._base_url = base_url.rstrip("/")

    async def generate(self, request: ModelRequest) -> ModelResponse:
        payload = {
            "model": request.model,
            "messages": [message.model_dump(exclude_none=True) for message in request.messages],
            "stream": False,
            "options": {
                "temperature": request.temperature,
            },
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self._base_url}/api/chat",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        return ModelResponse(
            provider=self.name,
            model=request.model,
            content=data["message"]["content"],
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/api/tags")
                response.raise_for_status()
        except httpx.HTTPError:
            return False

        return True

