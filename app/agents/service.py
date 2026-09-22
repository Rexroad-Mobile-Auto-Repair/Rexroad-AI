from __future__ import annotations

import json
from uuid import uuid4

from app.agents.models import AgentQueryRequest, AgentQueryResponse
from app.config import Settings
from app.journal.store import ActionJournal
from app.providers.factory import get_default_model
from app.providers.models import ModelRequest
from app.providers.registry import ProviderRegistry
from app.tools.models import ModelMessage
from app.tools.registry import ToolRegistry


class AgentLoopLimitError(RuntimeError):
    pass


class AgentService:
    def __init__(
        self,
        settings: Settings,
        registry: ProviderRegistry,
        tools: ToolRegistry | None = None,
        journal: ActionJournal | None = None,
        max_tool_rounds: int = 8,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._tools = tools
        self._journal = journal
        self._max_tool_rounds = max_tool_rounds

    async def query(self, request: AgentQueryRequest) -> AgentQueryResponse:
        provider_name = request.provider or self._settings.default_provider
        provider = self._registry.get(provider_name)

        model = request.model or get_default_model(
            self._settings,
            provider_name,
        )

        session_id = str(uuid4())

        messages = [
            ModelMessage(
                role="user",
                content=request.message,
            )
        ]

        tool_specs = (
            self._tools.specs()
            if self._tools is not None
            else []
        )

        for _ in range(self._max_tool_rounds + 1):
            response = await provider.generate(
                ModelRequest(
                    model=model,
                    messages=messages,
                    tools=tool_specs,
                )
            )

            if not response.tool_calls:
                return AgentQueryResponse(
                    provider=response.provider,
                    model=response.model,
                    session_id=session_id,
                    content=response.content,
                )

            if self._tools is None:
                raise RuntimeError(
                    "Provider requested tools but no tool registry is configured"
                )

            messages.append(
                ModelMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )

            for tool_call in response.tool_calls:
                tool = self._tools.get(tool_call.name)

                try:
                    result = self._tools.execute(
                        tool_call.name,
                        **tool_call.arguments,
                    )
                except Exception as exc:
                    if self._journal is not None:
                        self._journal.record(
                            session_id=session_id,
                            provider=response.provider,
                            model=response.model,
                            tool=tool_call.name,
                            permission=tool.permission,
                            arguments=tool_call.arguments,
                            status="error",
                            error=str(exc),
                        )

                    raise

                if isinstance(result, str):
                    content = result
                else:
                    content = json.dumps(
                        result,
                        ensure_ascii=False,
                        default=str,
                    )

                if self._journal is not None:
                    self._journal.record(
                        session_id=session_id,
                        provider=response.provider,
                        model=response.model,
                        tool=tool_call.name,
                        permission=tool.permission,
                        arguments=tool_call.arguments,
                        status="success",
                        result_preview=content[:1000],
                    )

                messages.append(
                    ModelMessage(
                        role="tool",
                        content=content,
                        tool_call_id=tool_call.id,
                        tool_name=tool_call.name,
                    )
                )

        raise AgentLoopLimitError(
            f"Tool loop exceeded {self._max_tool_rounds} rounds"
        )
