import json

import pytest
from pydantic import BaseModel

from app.providers.models import ModelRequest, ModelResponse
from app.structured_output import StructuredOutputError, StructuredOutputService
from app.tools.models import ModelMessage


class ExampleResult(BaseModel):
    summary: str
    files: list[str]


class FakeProvider:
    name = "openai_compatible"

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(provider=self.name, model=request.model, content=self.responses.pop(0))


def request() -> ModelRequest:
    return ModelRequest(model="test", messages=[ModelMessage(role="user", content="summarize")])


@pytest.mark.asyncio
async def test_valid_structured_result_is_typed_without_retry():
    provider = FakeProvider([json.dumps({"summary": "ok", "files": ["a.py"]})])
    result = await StructuredOutputService().generate(provider, request(), ExampleResult, name="example")
    assert result == ExampleResult(summary="ok", files=["a.py"])
    assert len(provider.requests) == 1
    assert provider.requests[0].structured_output.name == "example"


@pytest.mark.asyncio
async def test_invalid_result_gets_one_bounded_correction():
    provider = FakeProvider(["{bad", json.dumps({"summary": "fixed", "files": []})])
    result = await StructuredOutputService().generate(provider, request(), ExampleResult, name="example")
    assert result.summary == "fixed"
    assert len(provider.requests) == 2
    assert "JSON only" in provider.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_exhausted_validation_is_controlled_and_bounded():
    provider = FakeProvider(["{}", "{\"summary\": 3}"])
    with pytest.raises(StructuredOutputError, match="structured output validation failed"):
        await StructuredOutputService().generate(provider, request(), ExampleResult, name="example")
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_oversized_output_is_rejected_without_leaking_payload():
    provider = FakeProvider(["x" * 40_000])
    with pytest.raises(StructuredOutputError, match="size limit"):
        await StructuredOutputService().generate(provider, request(), ExampleResult, name="example", max_validation_attempts=1)
