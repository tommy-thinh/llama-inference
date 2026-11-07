# Phase 1 independent audit — 2026-11-08

Auditor: separate GPT-6.1-Sol agent, high reasoning, using `ponytail-audit`.
Scope: all project Python modules, tests, configuration, active documentation,
and curated evidence, at one local user, one inference slot, and serial load.

What this repo does: It builds and verifies a pinned CPU llama.cpp runtime and
Meta Llama 3.2 1B Instruct model, serves local HTTP requests, and records
reproducible benchmarks.

Healthy. Nothing remains to fix in the final Phase 1 snapshot. The two foreground
launcher defects found during review are resolved: launching verifies
model/runtime identity, and explicit CLI settings survive malformed inherited
environment values. All eight independent launcher regressions pass.

Verification:

- Audit ordinary run: 129 passed, seven intentional live skips before launcher
  tests were added; lint and formatting passed.
- Final saved live-suite JUnit: 144 tests, zero failures, errors, or skips.
- Baseline: 56 successful requests, seven clean server exits, no prefix reuse;
  summaries independently recompute.
- All eleven archived source files match benchmark hashes. Only foreground
  launch changed afterward; the measurement path is unchanged.
- Curated raw/archive hashes match verification records. Historical Qwen raw
  bytes remain unchanged.
- No remaining llama-server processes.

**Verdict: Healthy for Phase 1; ready to commit `Phase 1`.**

Not checked: upstream llama.cpp internals, broad model quality,
concurrent/public serving, and later phases. The auditor inspected saved live
evidence without starting another inference workload.
