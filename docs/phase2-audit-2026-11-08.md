# Phase 2 independent audit — 2026-11-08

Auditor: separate GPT-6.1-Sol agent, high reasoning, using `ponytail-audit`.
Scope: the repository, with emphasis on Phase 2 streaming ownership and UI state,
at one local user and serial CPU inference load.

What this repo does: It serves pinned CPU Llama inference through a local
streaming chat application.

## Must fix — resolved

**Timeout error delivery delayed upstream cleanup** (`backend/inference.py`).
Terminal error frames were yielded after the generation timeout scope but before
the upstream response closed. A client stalled while receiving that frame could
retain upstream resources indefinitely. The auditor reproduced an unfinished
request and an open upstream response at 0.20 seconds with a 0.05-second deadline.

The fix closes upstream before yielding every terminal error. The response
producer also bounds all sends, including error and final empty-body sends, by
the total deadline plus a documented 100 ms terminal-flush grace. Fourteen
independent regressions failed before the fix and pass unchanged afterward.
The auditor's original reproduction now confirms the request finishes and
upstream is closed before error sending starts.

## Final verification

Healthy. Nothing remains to fix in the reviewed local application.

- Independent ordinary run: 252 passed, eight intentional inference skips.
- Independent backend/deadline subset: 114 passed.
- Saved final live JUnit: 260 cases, zero failures, errors, or skips.
- All twelve recorded source hashes match current files.
- TCP smoke hash and contents agree: two progressive chunks, cancellation and
  native cancel log, recovery answer `4` in 476.56 ms, both processes reaped.
- Ruff lint and formatting pass. Documentation agrees on results and deadline grace.

**Verdict: Healthy for Phase 2's local serial scope.**

Not checked: graphical browser rendering, upstream runtime internals, or
concurrent/public deployment. The auditor inspected saved live evidence without
starting another native inference workload.
