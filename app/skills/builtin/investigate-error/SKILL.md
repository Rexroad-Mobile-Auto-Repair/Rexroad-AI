---
description: Investigate an error
model_invocable: false
allowed_tools:
  - filesystem.read
  - filesystem.grep
  - filesystem.glob
  - workspace.symbols
arguments:
  - name: target
    required: true
  - name: note
---
Read only. Read {{target}} first. Keep files unchanged. Investigate the supplied error context against the source. Distinguish confirmed causes from hypotheses and identify the smallest useful next diagnostic step.
User context: {{note}}
Cite actual source paths and lines. Keep the final report under 180 words. Do not create plans, run checks, approve patches, commit, or push.

No tests or checks are executed in this review. Never claim they passed, failed, or were run. Treat existing assertions as source evidence only. Do not list already-covered behavior as a coverage gap. If no meaningful gap is supported by the contract and source, say so plainly.
