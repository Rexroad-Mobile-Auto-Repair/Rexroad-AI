from __future__ import annotations

import json
from uuid import uuid4

from app.agents.models import AgentQueryRequest, AgentQueryResponse
from app.config import Settings
from app.context.builder import ContextBudgetError, ContextBuilder
from app.context.models import ContextRequest
from app.journal.store import ActionJournal
from app.providers.factory import get_default_model
from app.providers.models import ModelRequest
from app.providers.registry import ProviderRegistry
from app.tools.models import ModelMessage
from app.tools.registry import ToolRegistry

AGENT_WORKFLOW_PROMPT = (
    "You are operating inside Rexroad AI. "
    "Before answering, determine what evidence is needed. "
    "Use the available read-only tools when repository or workspace facts "
    "need to be checked. Do not claim that an action occurred unless tool "
    "output supports it. After using tools, produce a concise draft answer "
    "based on the evidence."
)

VERIFICATION_PROMPT = (
    "Verification pass: review the draft answer against the tool evidence "
    "already collected. Check for unsupported claims, missing evidence, or "
    "contradictions. Use additional available read-only tools if needed. "
    "Return the final answer only after verification is complete."
)


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
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._tools = tools
        self._journal = journal
        self._max_tool_rounds = max_tool_rounds
        self._context_builder = context_builder or ContextBuilder()

    async def query(self, request: AgentQueryRequest) -> AgentQueryResponse:
        provider_name = request.provider or self._settings.default_provider
        provider = self._registry.get(provider_name)

        model = request.model or get_default_model(
            self._settings,
            provider_name,
        )

        session_id = str(uuid4())

        if self._journal is not None:
            self._journal.append_event(
                session_id=session_id,
                event_type="user_request",
                payload={"content": request.message},
            )

        messages = [
            ModelMessage(
                role="system",
                content=AGENT_WORKFLOW_PROMPT,
            ),
            ModelMessage(
                role="user",
                content=request.message,
            ),
        ]

        tool_specs = (
            self._tools.specs()
            if self._tools is not None
            else []
        )

        tool_rounds = 0
        used_tools = False
        verification_requested = False

        while True:
            try:
                context = self._context_builder.build(
                    ContextRequest(
                        messages=messages,
                        total_byte_budget=self._settings.model_context_byte_budget,
                        tool_result_byte_budget=self._settings.model_tool_result_byte_budget,
                    )
                )
                response = await provider.generate(
                    ModelRequest(model=model, messages=context.messages, tools=tool_specs)
                )
            except ContextBudgetError:
                if self._journal is not None:
                    self._journal.append_event(
                        session_id=session_id,
                        event_type="error",
                        payload={"stage": "context"},
                    )
                raise
            except Exception:
                if self._journal is not None:
                    self._journal.append_event(
                        session_id=session_id,
                        event_type="error",
                        payload={"stage": "verification" if verification_requested else "provider"},
                    )
                raise

            if self._journal is not None:
                self._journal.append_event(
                    session_id=session_id,
                    event_type="model_response",
                    payload={"provider": response.provider, "model": response.model, "content": response.content},
                )

            if response.tool_calls:
                if self._tools is None:
                    if self._journal is not None:
                        self._journal.append_event(
                            session_id=session_id,
                            event_type="error",
                            payload={"stage": "tool_dispatch"},
                        )
                    raise RuntimeError(
                        "Provider requested tools but no tool registry is configured"
                    )

                tool_rounds += 1

                if tool_rounds > self._max_tool_rounds:
                    if self._journal is not None:
                        self._journal.append_event(session_id=session_id, event_type="error", payload={"stage": "loop_limit"})
                    raise AgentLoopLimitError(
                        f"Tool loop exceeded {self._max_tool_rounds} rounds"
                    )

                used_tools = True

                messages.append(
                    ModelMessage(
                        role="assistant",
                        content=response.content,
                        tool_calls=response.tool_calls,
                    )
                )

                for tool_call in response.tool_calls:
                    tool = self._tools.get(tool_call.name)
                    if self._journal is not None:
                        self._journal.append_event(
                            session_id=session_id,
                            event_type="tool_call",
                            tool_call_id=tool_call.id,
                            payload={"tool": tool_call.name},
                        )

                    try:
                        result = self._tools.execute(
                            tool_call.name,
                            **tool_call.arguments,
                        )
                    except Exception as exc:
                        action = None
                        if self._journal is not None:
                            action = self._journal.record(
                                session_id=session_id,
                                provider=response.provider,
                                model=response.model,
                                tool=tool_call.name,
                                permission=tool.permission,
                                arguments=tool_call.arguments,
                                status="error",
                                error=str(exc),
                            )

                        if self._journal is not None:
                            self._journal.append_event(
                                session_id=session_id,
                                event_type="tool_result",
                                tool_call_id=tool_call.id,
                                payload={"status": "error"},
                                action_id=action.id if action is not None else None,
                            )
                            self._journal.append_event(
                                session_id=session_id,
                                event_type="error",
                                tool_call_id=tool_call.id,
                                payload={"stage": "tool"},
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

                    action = None
                    if self._journal is not None:
                        action = self._journal.record(
                            session_id=session_id,
                            provider=response.provider,
                            model=response.model,
                            tool=tool_call.name,
                            permission=tool.permission,
                            arguments=tool_call.arguments,
                            status="success",
                            result_preview=content[:1000],
                        )

                        self._journal.append_event(
                            session_id=session_id,
                            event_type="tool_result",
                            action_id=action.id,
                            tool_call_id=tool_call.id,
                            payload={"status": "success"},
                        )

                    messages.append(
                        ModelMessage(
                            role="tool",
                            content=content,
                            tool_call_id=tool_call.id,
                            tool_name=tool_call.name,
                        )
                    )

                continue

            if used_tools and not verification_requested:
                verification_requested = True

                if self._journal is not None:
                    self._journal.append_event(
                        session_id=session_id,
                        event_type="verification_request",
                        payload={"reason": "tool_evidence_review"},
                    )

                messages.append(
                    ModelMessage(
                        role="assistant",
                        content=response.content,
                    )
                )

                messages.append(
                    ModelMessage(
                        role="user",
                        content=VERIFICATION_PROMPT,
                    )
                )

                continue

            if self._journal is not None:
                self._journal.append_event(
                    session_id=session_id,
                    event_type="verification_response" if verification_requested else "final_response",
                    payload={"provider": response.provider, "model": response.model, "content": response.content},
                )
                if verification_requested:
                    self._journal.append_event(
                        session_id=session_id,
                        event_type="final_response",
                        payload={"provider": response.provider, "model": response.model, "content": response.content},
                    )

            return AgentQueryResponse(
                provider=response.provider,
                model=response.model,
                session_id=session_id,
                content=response.content,
            )
