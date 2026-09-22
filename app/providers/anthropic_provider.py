from anthropic import AsyncAnthropic

from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse


class AnthropicProvider(ModelProvider):
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self._client = client or AsyncAnthropic(api_key=api_key)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        response = await self._client.messages.create(
            model=request.model,
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=4096,
        )

        content = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text"
        )

        return ModelResponse(
            provider=self.name,
            model=request.model,
            content=content,
        )

    async def health_check(self) -> bool:
        try:
            await self._client.models.list()
        except Exception:  # noqa: BLE001 - health checks must fail closed.
            return False

        return True

