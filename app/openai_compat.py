import time
from collections.abc import AsyncIterator
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.models import AgentQueryRequest, AgentQueryResponse
from app.agents.service import AgentLoopLimitError, AgentService, AgentSessionError
from app.policy.workspaces import WorkspaceAccessError

REXROAD_MODEL = "rexroad-ai"
MAX_MESSAGES = 100
MAX_MESSAGE_CHARS = 12_000


class OpenAIMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str

    @field_validator("content")
    @classmethod
    def bounded_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message content must not be empty")
        if len(value) > MAX_MESSAGE_CHARS:
            raise ValueError("Message content is too long")
        return value


class OpenAIChatRequest(BaseModel):
    model: str
    messages: list[OpenAIMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    stream: bool = False
    temperature: float | None = Field(default=None, ge=0, le=2)
    user: str | None = Field(default=None, max_length=200)
    tools: list[dict[str, Any]] | None = None
    functions: list[dict[str, Any]] | None = None
    model_config = ConfigDict(extra="ignore")


def validate_request(request: OpenAIChatRequest) -> None:
    if request.model != REXROAD_MODEL:
        raise HTTPException(status_code=400, detail=_error("Model is not available", "invalid_model"))
    if request.tools is not None or request.functions is not None:
        raise HTTPException(
            status_code=400,
            detail=_error("Client-defined tools are not supported by Rexroad AI compatibility API.", "unsupported_tools"),
        )


def _error(message: str, code: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": "invalid_request_error", "param": None, "code": code}}


def to_agent_request(request: OpenAIChatRequest, *, session_id: str | None, workspace: str | None) -> AgentQueryRequest:
    transcript = "\n\n".join(f"{message.role}: {message.content}" for message in request.messages)
    return AgentQueryRequest(message=transcript, session_id=session_id, workspace=workspace)


def completion_response(response: AgentQueryResponse, *, completion_id: str | None = None) -> dict[str, Any]:
    return {
        "id": completion_id or f"chatcmpl-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": REXROAD_MODEL,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": response.content}, "finish_reason": "stop"}],
    }


def chunk(*, completion_id: str, content: str | None = None, finish_reason: str | None = None) -> dict[str, Any]:
    delta: dict[str, str] = {}
    if content is not None:
        delta["content"] = content
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": REXROAD_MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


async def stream_completion(agent: AgentService, request: AgentQueryRequest, completion_id: str) -> AsyncIterator[dict[str, Any]]:
    async for event in agent.query_stream(request):
        event_type = event.get("type")
        if event_type == "text_delta" and event.get("text"):
            yield chunk(completion_id=completion_id, content=event["text"])
        elif event_type == "completed":
            yield chunk(completion_id=completion_id, finish_reason="stop")


def safe_agent_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AgentSessionError):
        status, code, message = 404, "invalid_session", str(exc)
    elif isinstance(exc, AgentLoopLimitError):
        status, code, message = 422, "tool_loop_limit", "The request exceeded the bounded tool-use limit"
    elif isinstance(exc, WorkspaceAccessError):
        status, code, message = 400, "invalid_workspace", "Workspace is unavailable"
    else:
        status, code, message = 502, "provider_error", "The request could not be completed safely"
    return HTTPException(status_code=status, detail=_error(message[:200], code))
