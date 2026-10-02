# External Reference Notes

These notes record read-only architectural references reviewed during the
OpenAI-compatible API work. No external framework is vendored into Rexroad.

## `instructor-main.zip`

- License: MIT (`instructor-main/LICENSE`).
- Useful reference: validation of OpenAI `ChatCompletion` and
  `ChatCompletionChunk` shapes using the installed OpenAI SDK types.
- Applied lesson: keep response/chunk serialization standards-compatible and
  validate deterministic fixtures with SDK models.
- Not adopted: provider patching, retry/correction behavior, and framework
  integration.

## `headlong-main.zip`

- License: Apache 2.0 (`headlong-main/LICENSE`).
- Useful reference: trajectory lineage and explicit sub-agent trace concepts.
- Not adopted: recursive decomposition and broad shell-backed autonomy, which
  conflict with Rexroad's bounded supervisor and capability boundaries.

## `llm-engineer-toolkit-main.zip`

- License: Apache 2.0.
- Useful reference: high-level framework comparison only.
- No runtime code was needed for Rexroad's compatibility boundary.

## `ai_agent_framework_crash_course.zip` and `starter_ai_agents.zip`

- Read-only educational examples were inspected for lifecycle and tool-callback
  patterns.
- No clear repository license file was found in either archive, so no code was
  copied or adapted.

## Reuse policy

Prefer protocol documentation and installed SDK type validation over copying
framework internals. Re-evaluate licenses before adding any future third-party
source or dependency.

## `Clawd-Code-main.zip`

- Verified local archive: `C:\Users\Aaron\Downloads\Clawd-Code-main.zip`.
- License: MIT, copyright 2026 Clawd Codex Team, in `Clawd-Code-main/LICENSE`.
- References: `src/tool_system/agent_loop.py`, `tools/plan_mode.py`,
  `tools/team.py`, and the agent/session and permission modules.
- Applied ideas: bounded tool loops, persisted review plans, independent
  read-only analysts, saved evidence comparison, and supervised patch execution.
  Rexroad implements these through its own agent, plan, team, journal, and
  scoped approval services; the donor is a reference, not a runtime dependency.
- Kept Rexroad's existing workspace confinement and one-use patch approvals.
  Donor Bash tools, session stores, permissions, and team files were not imported.
- No donor source was copied in this maintenance change.

## Local startup

Run `Start-RexroadAI.ps1` from the repository root to bring up the existing
Qwen model, LM Studio API, and Rexroad backend together. It preserves a running
backend and does not change Windows login settings. A healthy `/health` response
alone confirms the backend; the launcher separately checks model availability.
