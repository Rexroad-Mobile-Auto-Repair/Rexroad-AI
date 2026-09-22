import json

from openai import AsyncOpenAI

from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse
from app.tools.models import ModelMessage, ToolCall, ToolSpec


class OpenAIProvider(ModelProvider):
    name = "openai"

    def __init__(
        self,
        api_key: str,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._client = client or AsyncOpenAI(api_key=api_key)

    @staticmethod
    def _encode_tool_name(name: str) -> str:
        return name.replace(".", "__")

    @staticmethod
    def _decode_tool_name(name: str) -> str:
        return name.replace("__", ".")

    @classmethod
    def _tool_payload(cls, tool: ToolSpec) -> dict:
        return {
            "type": "function",
            "name": cls._encode_tool_name(tool.name),
            "description": tool.description,
            "parameters": tool.parameters,
        }

    @classmethod
    def _message_payload(
        cls,
        message: ModelMessage,
    ) -> list[dict]:
        if message.role == "tool":
            if not message.tool_call_id:
                raise ValueError(
                    "Tool messages require tool_call_id"
                )

            return [
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content,
                }
            ]

        items: list[dict] = []

        if message.content:
            items.append(
                {
                    "role": message.role,
                    "content": message.content,
                }
            )

        if message.role == "assistant":
            for tool_call in message.tool_calls:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": tool_call.id,
                        "name": cls._encode_tool_name(
                            tool_call.name
                        ),
                        "arguments": json.dumps(
                            tool_call.arguments,
                            ensure_ascii=False,
                        ),
                    }
                )

        return items

    @staticmethod
    def _parse_arguments(arguments: object) -> dict:
        if isinstance(arguments, dict):
            return arguments

        if not isinstance(arguments, str):
            raise TypeError(
                "Tool-call arguments must be a JSON object or JSON string"
            )

        parsed = json.loads(arguments)

        if not isinstance(parsed, dict):
            raise TypeError(
                "Tool-call arguments must decode to an object"
            )

        return parsed

    async def generate(self, request: ModelRequest) -> ModelResponse:
        input_items = [
            item
            for message in request.messages
            for item in self._message_payload(message)
        ]

        payload = {
            "model": request.model,
            "input": input_items,
            "temperature": request.temperature,
        }

        if request.tools:
            payload["tools"] = [
                self._tool_payload(tool)
                for tool in request.tools
            ]

        response = await self._client.responses.create(
            **payload
        )

        tool_calls = [
            ToolCall(
                id=item.call_id,
                name=self._decode_tool_name(
                    item.name
                ),
                arguments=self._parse_arguments(
                    item.arguments
                ),
            )
            for item in response.output
            if getattr(item, "type", None)
            == "function_call"
        ]

        return ModelResponse(
            provider=self.name,
            model=request.model,
            content=response.output_text or "",
            tool_calls=tool_calls,
        )

    async def health_check(self) -> bool:
        try:
            await self._client.models.list()
        except Exception:  # noqa: BLE001 - health checks must fail closed.
            return False

        return True
