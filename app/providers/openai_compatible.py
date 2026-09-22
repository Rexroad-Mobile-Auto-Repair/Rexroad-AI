import json

import httpx

from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse
from app.tools.models import ModelMessage, ToolCall, ToolSpec


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

    @staticmethod
    def _tool_payload(tool: ToolSpec) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }

    @staticmethod
    def _message_payload(message: ModelMessage) -> dict:
        if message.role == "tool":
            if not message.tool_call_id:
                raise ValueError("Tool messages require tool_call_id")

            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content,
            }

        payload = {
            "role": message.role,
            "content": message.content,
        }

        if message.role == "assistant" and message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": json.dumps(
                            tool_call.arguments,
                            ensure_ascii=False,
                        ),
                    },
                }
                for tool_call in message.tool_calls
            ]

        return payload

    @staticmethod
    def _parse_arguments(arguments: object) -> dict:
        if isinstance(arguments, dict):
            return arguments

        if not isinstance(arguments, str):
            raise ValueError(
                "Tool-call arguments must be a JSON object or JSON string"
            )

        parsed = json.loads(arguments)

        if not isinstance(parsed, dict):
            raise ValueError(
                "Tool-call arguments must decode to an object"
            )

        return parsed

    async def generate(self, request: ModelRequest) -> ModelResponse:
        payload = {
            "model": request.model,
            "messages": [
                self._message_payload(message)
                for message in request.messages
            ],
            "temperature": request.temperature,
        }

        if request.tools:
            payload["tools"] = [
                self._tool_payload(tool)
                for tool in request.tools
            ]

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()

        message = data["choices"][0]["message"]

        tool_calls = [
            ToolCall(
                id=str(tool_call["id"]),
                name=tool_call["function"]["name"],
                arguments=self._parse_arguments(
                    tool_call["function"].get("arguments", "{}")
                ),
            )
            for tool_call in message.get("tool_calls", [])
        ]

        return ModelResponse(
            provider=self.name,
            model=request.model,
            content=message.get("content") or "",
            tool_calls=tool_calls,
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

