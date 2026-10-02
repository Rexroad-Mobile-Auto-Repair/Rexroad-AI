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

## Supervised coding panel

Open `http://127.0.0.1:8000/operator`, select the registered project, and use
one scope name for the work. Describe a narrow change and choose Create coding workflow.
Analyze first, read and accept the saved report, then generate proposed changes.
Review the displayed diff before accepting the proposal and execution steps.
Approve each file separately, apply the approved changes, run checks, and review
the verifier's result. A completed workflow must show Verified and passed checks.
Reject an incomplete proposal and enter corrections in Revision instructions.
Request a revision generates another reviewable proposal from the accepted source
snapshots, with a link to the rejected parent; it does not repeat analysis.

Each action reuses the existing persisted workflow, proposal, specification,
approval, and execution services. Generating a proposal does not edit files.
File approvals remain scoped and single-use. The panel prevents duplicate clicks
while a request is running; proposal generation also serializes requests for the
same workflow in the running backend.
Structured proposal generation sends the JSON schema to LM Studio using its
[documented response format](https://lmstudio.ai/docs/developer/openai-compat/structured-output).

If the model is unavailable or a request times out, keep the scope and saved task.
Run the startup script and reopen its guidance. A failed analysis with no executed
changes offers Retry analysis as a new task; it retains the failed attempt. Never
reapply an already executed patch to recover a failed check. Review the current
saved state and checks first. Refreshing the page does not approve or apply work.
After an applied implementation fails a check, Retry checks without reapplying
files creates a separate check-only plan. The previous failed plan and execution
attempt stay saved, and the consumed file approvals remain consumed. This option
does not recover failed patch execution or rejected verification. Pytest temporary
files are confined to `.pytest-tmp/checks` inside the selected project.
Saved check previews keep pass/fail, exit code, and timeout fields parseable even
when test output is long; only a short diagnostic excerpt is retained.
Rejected verifier reports have a separate read-only retry after successful saved
checks. Verification receives the current approved file paths and successful
check trace references. Neither retry accepts a report or grants file approval.
During acceptance, the local model omitted a requested test from multiple draft
revisions and produced incorrect verifier reports. The supervisor corrected
the missing test in a saved revision and rejected the incorrect reports. Continue
reviewing generated drafts and verification claims against source and check evidence.
Registered checks run with Rexroad AI's Python environment. For the SEO crawler,
that environment also needs the dependencies in the crawler's requirements file.
The existing Beautiful Soup, lxml, requests, and Google authentication dependencies
were added for this acceptance run; no website credentials are needed for report tests.

Source lines sent to the model are numbered. Final line references are retained
only when a unique exact quotation supports them; unsupported line references
are replaced with a reference to the saved source. This grounds citation anchors,
not every statement made by the model.
