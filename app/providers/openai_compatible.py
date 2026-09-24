import json

import httpx

from app.providers.base import ModelProvider
from app.providers.models import ModelRequest, ModelResponse, ProviderStreamEvent
from app.tools.models import ModelMessage, ToolCall, ToolSpec


class _ToolCallAccumulator:
    MAX_ARGUMENT_BYTES = 16_000

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, str]] = {}
        self._completed = False

    def add(self, fragment: dict) -> None:
        index = int(fragment.get("index", 0))
        current = self._calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
        current["id"] = current["id"] or str(fragment.get("id") or "")
        function = fragment.get("function") or {}
        current["name"] += str(function.get("name") or "")
        current["arguments"] += str(function.get("arguments") or "")
        if len(current["arguments"].encode("utf-8")) > self.MAX_ARGUMENT_BYTES:
            raise ValueError("tool-call arguments exceeded the size limit")

    def complete(self) -> list[ToolCall]:
        if self._completed:
            return []
        self._completed = True
        calls = []
        for index in sorted(self._calls):
            item = self._calls[index]
            if not item["id"] or not item["name"]:
                raise ValueError("incomplete streamed tool call")
            try:
                arguments = json.loads(item["arguments"] or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError("malformed streamed tool arguments") from exc
            if not isinstance(arguments, dict):
                raise TypeError("streamed tool arguments must be an object")
            calls.append(ToolCall(id=item["id"], name=item["name"], arguments=arguments))
        return calls


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
                raise TypeError("Tool messages require tool_call_id")

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

    async def stream(self, request: ModelRequest):
        payload = {
            "model": request.model,
            "messages": [self._message_payload(message) for message in request.messages],
            "temperature": request.temperature,
            "stream": True,
        }
        if request.tools:
            payload["tools"] = [self._tool_payload(tool) for tool in request.tools]
        text = ""
        accumulator = _ToolCallAccumulator()
        async with httpx.AsyncClient(timeout=120.0) as client, client.stream("POST", f"{self._base_url}/chat/completions", json=payload, headers=self._headers()) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    value = line[5:].strip()
                    if value == "[DONE]":
                        break
                    try:
                        chunk = json.loads(value)
                    except json.JSONDecodeError:
                        yield ProviderStreamEvent(type="provider_error", message="malformed provider stream")
                        return
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    for fragment in delta.get("tool_calls", []) or []:
                        accumulator.add(fragment)
                    part = delta.get("content") or ""
                    if part:
                        text += part
                        if len(text.encode("utf-8")) > 12000:
                            yield ProviderStreamEvent(type="provider_error", message="provider response exceeded the size limit")
                            return
                        yield ProviderStreamEvent(type="text_delta", text=part)
        try:
            calls = accumulator.complete()
        except ValueError as exc:
            yield ProviderStreamEvent(type="provider_error", message=str(exc))
            return
        for call in calls:
            yield ProviderStreamEvent(type="tool_call_complete", tool_call=call)
        yield ProviderStreamEvent(type="completed", response=ModelResponse(provider=self.name, model=request.model, content=text, tool_calls=calls))

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




