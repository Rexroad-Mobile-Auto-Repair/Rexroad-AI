from openai import AsyncOpenAI

from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse


class OpenAIProvider(ModelProvider):
    name = "openai"

    def __init__(
        self,
        api_key: str,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._client = client or AsyncOpenAI(api_key=api_key)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        response = await self._client.responses.create(
            model=request.model,
            input=request.messages,
            temperature=request.temperature,
        )

        return ModelResponse(
            provider=self.name,
            model=request.model,
            content=response.output_text,
        )

    async def health_check(self) -> bool:
        try:
            await self._client.models.list()
        except Exception:
            return False

        return True
