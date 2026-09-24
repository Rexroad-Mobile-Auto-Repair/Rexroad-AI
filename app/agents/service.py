from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from app.agents.context import (
    build_agent_system_context,
    read_only_intent,
    requests_check_execution,
)
from app.agents.models import AgentQueryRequest, AgentQueryResponse
from app.config import Settings
from app.context.builder import ContextBudgetError, ContextBuilder
from app.context.models import ContextRequest
from app.journal.store import ActionJournal
from app.knowledge.serialization import serialize_search_results
from app.providers.factory import get_default_model
from app.providers.models import ModelRequest
from app.providers.registry import ProviderRegistry
from app.tools.models import ModelMessage
from app.tools.registry import ToolRegistry

AGENT_WORKFLOW_PROMPT = "Before answering, determine what evidence is needed. Use available read-only tools when facts need to be checked."

VERIFICATION_PROMPT = (
    "Verification pass: review the draft answer against the tool evidence "
    "already collected. Check for unsupported claims, missing evidence, or "
    "contradictions. Use additional available read-only tools if needed. "
    "Return the final answer only after verification is complete. Answer the user's request directly; do not lead with internal verification commentary."
)


class AgentLoopLimitError(RuntimeError):
    pass


class AgentSessionError(ValueError):
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
        allow_tools_without_workspace: bool = True,
        include_identity_context: bool = False,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._tools = tools
        self._journal = journal
        self._max_tool_rounds = max_tool_rounds
        self._context_builder = context_builder or ContextBuilder()
        self._allow_tools_without_workspace = allow_tools_without_workspace
        self._include_identity_context = include_identity_context

    @staticmethod
    def _history_messages(journal: ActionJournal, session_id: str, budget: int) -> list[ModelMessage]:
        events = journal.list_events_for_session(session_id)
        messages: list[ModelMessage] = []
        used = 0
        for event in reversed(events):
            if event.event_type not in {"user_request", "final_response"}:
                continue
            content = event.payload.get("content")
            if not isinstance(content, str) or not content:
                continue
            message = ModelMessage(role="user" if event.event_type == "user_request" else "assistant", content=content)
            size = len(content.encode("utf-8"))
            if messages and used + size > budget:
                break
            messages.append(message)
            used += size
        return list(reversed(messages))

    def _tool_specs_for_request(self, request: AgentQueryRequest):
        if self._tools is None or not (request.workspace or self._allow_tools_without_workspace):
            return []
        check_allowed = requests_check_execution(request.message) and not read_only_intent(request.message)
        specs = []
        for spec in self._tools.specs():
            definition = self._tools.get(spec.name)
            if definition.permission == "read" or (definition.permission == "workspace_check" and check_allowed):
                specs.append(spec)
        return specs

    async def query(self, request: AgentQueryRequest) -> AgentQueryResponse:
        provider_name = request.provider or self._settings.default_provider
        provider = self._registry.get(provider_name)

        model = request.model or get_default_model(
            self._settings,
            provider_name,
        )

        session_id = request.session_id or str(uuid4())
        history: list[ModelMessage] = []
        if request.session_id:
            if self._journal is None or self._journal.get_session(session_id) is None:
                raise AgentSessionError("Session not found")
            prior = self._journal.list_events_for_session(session_id)
            first = next((event for event in prior if event.event_type == "user_request"), None)
            established_workspace = first.payload.get("workspace") if first else None
            if established_workspace != request.workspace:
                raise AgentSessionError("Workspace context cannot change during a session")
            history = self._history_messages(
                self._journal,
                session_id,
                min(12000, max(1000, self._settings.model_context_byte_budget // 2)),
            )

        if self._journal is not None:
            self._journal.append_event(
                session_id=session_id,
                event_type="user_request",
                payload={"content": request.message, "workspace": request.workspace},
            )

        tool_specs = self._tool_specs_for_request(request)
        allowed_tool_names = {spec.name for spec in tool_specs}

        system_context = AGENT_WORKFLOW_PROMPT
        if self._include_identity_context:
            system_context = build_agent_system_context(workspace=request.workspace, tools=tool_specs) + "\n" + AGENT_WORKFLOW_PROMPT

        messages = [
            ModelMessage(
                role="system",
                content=system_context,
            ),
            *history,
            ModelMessage(
                role="user",
                content=(f"Selected workspace context: {request.workspace}.\n" if request.workspace else "") + request.message,
            ),
        ]

        tool_rounds = 0
        used_tools = False
        verification_requested = False
        previous_tool_progress: set[str] = set()

        while True:
            try:
                context = self._context_builder.build(
                    ContextRequest(
                        messages=messages,
                        total_byte_budget=self._settings.model_context_byte_budget,
                        tool_result_byte_budget=self._settings.model_tool_result_byte_budget,
                        max_evidence_items=self._settings.model_max_evidence_items,
                        total_evidence_byte_budget=self._settings.model_total_evidence_byte_budget,
                        evidence_content_byte_budget=self._settings.model_evidence_content_byte_budget,
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
                    if tool_call.name not in allowed_tool_names:
                        if self._journal is not None:
                            self._journal.append_event(session_id=session_id, event_type="error", payload={"stage": "tool_policy"})
                        raise RuntimeError("Requested tool is not available for this request")
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

                    progress_key = hashlib.sha256((tool_call.name + json.dumps(tool_call.arguments, sort_keys=True, default=str) + json.dumps(result, sort_keys=True, default=str)).encode()).hexdigest()
                    if tool_rounds > 2 and progress_key in previous_tool_progress:
                        if self._journal is not None:
                            self._journal.append_event(session_id=session_id, event_type="error", payload={"stage": "no_progress"})
                        raise AgentLoopLimitError("Repeated tool call made no progress")
                    previous_tool_progress.add(progress_key)

                    if isinstance(result, str):
                        content = result
                    elif tool_call.name == "knowledge.search":
                        content = serialize_search_results(result)
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
