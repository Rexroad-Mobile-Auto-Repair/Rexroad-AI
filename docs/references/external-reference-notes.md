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

## Follow-up reliability milestone

The dashboard keeps failed plans and worker reports intact. Failed check plans
move to Task history only when a replacement check plan led to a verified coding
workflow. Stopped read-only tasks also appear in history and remain inspectable
in Coding Jobs. Failures involving patch execution stay in Needs Attention.

A workflow can declare required_test_paths. When an objective explicitly requests
adding or updating tests and names existing Python test files, those paths are
also recorded automatically. Proposals must change each required test file and
include a pytest check covering them before they can be saved for review. This
checks completeness, not the quality or meaning of an assertion. Read-only test
reviews do not require test changes, and no input-validation contract is invented.

Before accepting verification, the normal runtime compares saved check traces,
approved patch hashes, current files, and the verifier's audited source snapshots.
Missing or stale evidence prevents acceptance. Direct claims that tests failed
are flagged when saved checks passed; historical and negated failure descriptions
are excluded. This bounded wording check does not prove every model statement.
Conflicts are displayed in the panel; rejection remains available for a new
read-only verification attempt. File approvals are never granted by verification.

Live follow-up acceptance used the existing acceptance_test workspace rather than
expanding the journal workspace's permissions. Workflow
ed8ba316-a08f-47b0-9a99-dac7a768fd26 added Unicode-name and surrounding-space
tests to test_example.py, preserving the prior example.py edit. Its first model
proposal contained both requested tests; registered pytest and git_diff_check
passed and independent source verification was accepted. Startup remains
Start-RexroadAI.ps1. The workflow and reports are saved in scope followup-reliability.

A separate normal-provider/API completeness probe rejected a proposal omitting
the requested test file before saving a proposal or creating any execution trace.
That probe was cancelled after verification; it granted no file approval.

## Clawd-Code project search integration

The first donor feature is file finding and literal source search. Small traversal
and filename-matching helpers were adapted from
Clawd-Code-main/src/tool_system/tools/grep.py in the supplied archive, with
glob.py reviewed for the file-finding interface. The archive SHA256 is
1b5fdb379cf6c983f4e1fc0e3b434e658cac7622f0017759c87ad7e0acb33e6d.
The original MIT notice is retained in third_party/clawd_code/LICENSE;
app/tools/code_search.py identifies the adaptation. No donor framework or
runtime was imported, and this feature adds no dependency.

The operator panel's Search project card provides Search text and Find files.
Normal agents and read-only analyst profiles also receive filesystem.grep and
filesystem.glob through the existing registry, permission checks, and journal.
GET /workspaces/{workspace}/search uses the same implementation. Search stays
inside existing registered workspace roots and does not approve or modify files.

Results include workspace-relative filenames, source line numbers, and bounded
context. This milestone searches literal text, not regular expressions. A file
filter such as *.py narrows the scan. Generated directories, environment files,
credential-named files, symbolic links, binary files, and oversized files are
excluded. Limits cover results, scan time, visited files, read bytes, and content
output; partial results are identified. Search excerpts do not replace reading
the complete source before analysis or editing. Existing filesystem.search remains.

Normal LM Studio/provider acceptance in session
c810f541-c6a8-4fbe-bf12-20a92922a72e successfully journaled filesystem.glob,
filesystem.grep, and filesystem.read in acceptance_test and cited greeting's
declaration at example.py line 1. The visible operator search independently found
_filter_form in seo_crawler at app/reports/html_view.py line 96.

### Source viewer

Search-result filenames and matching lines now open a read-only source viewer.
GET /workspaces/{workspace}/source validates registered workspace paths and uses
the search service's text-file and private/generated-file exclusions. It reads
at most 256 KB and returns up to 120 numbered lines around the requested line.
The panel highlights that line and offers Previous lines, Next lines, and Close
file. Empty files are identified; a stale line reference requires a new search.
Source text is rendered without interpreting HTML. Project changes abort pending
reads and clear the viewer; another file selection replaces the previous request.
No source file is modified and no approval is granted by viewing it.

Live browser acceptance opened seo_crawler/app/reports/html_view.py at line 96,
navigated both directions, cleared the viewer on workspace change, and opened
acceptance_test/example.py from Find files. The viewer controls fit a 390px
viewport without horizontal page overflow. This extends Rexroad's donor-inspired
search integration; the viewer itself is Rexroad code, with no new donor imports.

### Python code navigation

Find definitions and Find references use Python's AST parser through the new
workspace.symbols read-only tool and GET /workspaces/{workspace}/symbols.
Definitions cover named functions, async functions, methods, and classes.
Possible references cover name loads, attribute loads, and import names/aliases;
comments and plain string literals are excluded. Results link to the existing
source viewer. These are name-based syntax candidates, not resolved bindings or
confirmed callers. Only Python is supported in this milestone, with no language
server installation. Invalid Python files are skipped and counted, and normal
search traversal, read-byte, output, result, and workspace limits remain in place.

Clawd-Code's tools/lsp.py interface was reviewed as a feature reference. Its
implementation delegates requests to a separately configured LSP client; that
runtime was not copied or installed. Rexroad's app/tools/symbols.py supplies this
smaller navigation implementation using the standard library, and read-only
analyst profiles can use it through the existing tool registry.

Live normal-provider session 0ca8950c-d6ba-4d9d-a305-b66fe3cc960e successfully
used workspace.symbols successfully for both definitions and references.
It found greeting at example.py line 1 and five possible references in
test_example.py. Browser acceptance found the same candidates and opened the
Unicode-name test at line 13 in the read-only source viewer.
