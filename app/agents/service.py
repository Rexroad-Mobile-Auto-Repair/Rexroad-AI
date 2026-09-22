from app.agents.models import AgentQueryRequest, AgentQueryResponse
from app.config import Settings
from app.providers.factory import get_default_model
from app.providers.models import ModelRequest
from app.providers.registry import ProviderRegistry
from app.tools.models import ModelMessage


class AgentService:
    def __init__(
        self,
        settings: Settings,
        registry: ProviderRegistry,
    ) -> None:
        self._settings = settings
        self._registry = registry

    async def query(self, request: AgentQueryRequest) -> AgentQueryResponse:
        provider_name = request.provider or self._settings.default_provider
        provider = self._registry.get(provider_name)

        model = request.model or get_default_model(
            self._settings,
            provider_name,
        )

        response = await provider.generate(
            ModelRequest(
                model=model,
                messages=[
                    ModelMessage(
                        role="user",
                        content=request.message,
                    )
                ],
            )
        )

        return AgentQueryResponse(
            provider=response.provider,
            model=response.model,
            content=response.content,
        )
