import httpx

from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse


class OpenAICompatibleProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            return {}

        return {
            "Authorization": f"Bearer {self._api_key}",
        }

    async def generate(self, request: ModelRequest) -> ModelResponse:
        payload = {
            "model": request.model,
            "messages": request.messages,
            "temperature": request.temperature,
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()

        return ModelResponse(
            provider=self.name,
            model=request.model,
            content=data["choices"][0]["message"]["content"],
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{self._base_url}/models",
                    headers=self._headers(),
                )
                response.raise_for_status()
        except httpx.HTTPError:
            return False

        return True
