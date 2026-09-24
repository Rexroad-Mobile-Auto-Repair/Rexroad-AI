# Rexroad OpenAI Compatibility Reference

The compatibility surface is an adapter over Rexroad's existing `AgentService`.
It is not a provider passthrough.

## Supported surface

- `GET /v1/models` exposes the stable model ID `rexroad-ai`.
- `POST /v1/chat/completions` accepts bounded `system`, `user`, and `assistant`
  messages, plus `stream` and an optional bounded `temperature`.
- `stream=true` emits OpenAI `chat.completion.chunk` objects followed by
  `data: [DONE]`.
- `X-Rexroad-Workspace` selects an explicitly registered workspace.
- `X-Rexroad-Session-ID` continues an existing Rexroad session.
- Non-streaming responses return `X-Rexroad-Session-ID` for the authoritative
  persisted session.
- For a newly created streaming session, the standard stream remains free of
  Rexroad-specific events and therefore does not expose that session ID in a
  response header; clients that need continuation should first use a
  non-streaming request or supply an existing session ID.

## Deliberate boundaries

- Client-defined tools and functions are rejected.
- Clients cannot select providers, internal model names, endpoints, or keys.
- Client system messages are conversation context below Rexroad's own identity,
  safety, workspace, and tool-policy context.
- Workspace selection is never inferred from message text.
- Usage counts are omitted because the provider abstraction does not expose
  authoritative token accounting.
- The compatibility endpoint is intended for localhost use unless a future
  authenticated deployment boundary is added.

## Maintenance rule

Keep this adapter thin. New behavior should be implemented in the existing
Rexroad services first, then translated at the compatibility boundary. Never
duplicate the agent loop or expose internal capability objects.
