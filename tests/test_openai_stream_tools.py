import httpx
import pytest

from app.providers.models import ModelRequest
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.tools.models import ModelMessage


def request() -> ModelRequest:
    return ModelRequest(model="test", messages=[ModelMessage(role="user", content="inspect")])


@pytest.mark.asyncio
async def test_interleaved_streamed_tool_calls_are_reconstructed(monkeypatch):
    lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"a","function":{"name":"filesystem."}},{"index":1,"id":"b","function":{"name":"git."}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"read","arguments":"{\\"path\\":\\"a.py\\"}"}},{"index":1,"function":{"name":"status","arguments":"{\\"workspace\\":\\"repo\\"}"}}]}}]}',
        "data: [DONE]",
    ]

    class Response:
        def raise_for_status(self): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def aiter_lines(self):
            for line in lines: yield line

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    events = [event async for event in OpenAICompatibleProvider("http://test").stream(request())]
    calls = [event.tool_call for event in events if event.type == "tool_call_complete"]
    assert [(call.id, call.name) for call in calls] == [("a", "filesystem.read"), ("b", "git.status")]
    assert calls[0].arguments == {"path": "a.py"}
    assert calls[1].arguments == {"workspace": "repo"}


@pytest.mark.asyncio
async def test_malformed_streamed_tool_arguments_fail_safely(monkeypatch):
    lines = ['data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"a","function":{"name":"git.status","arguments":"{bad"}}]}}]}', "data: [DONE]"]

    class Response:
        def raise_for_status(self): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def aiter_lines(self):
            for line in lines: yield line
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs): return Response()
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    events = [event async for event in OpenAICompatibleProvider("http://test").stream(request())]
    assert [event.type for event in events] == ["provider_error"]
