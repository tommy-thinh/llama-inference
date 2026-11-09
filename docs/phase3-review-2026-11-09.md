# Phase 3 independent change review — 2026-11-09

What this change does: PostgreSQL now stores anonymous sessions, conversations,
messages, and measured inference requests. The browser restores saved history,
and generation commits its final state before reporting success.

This review used the `ponytail-review` skill, `PROJECT_SPEC.md` Phase 3,
`docs/07-database-design.md`, and `docs/08-sessions-and-persistence.md`. Scope was
the uncommitted persistence change and connected inference/frontend code. Expected
load is the documented local application with one backend worker. Phase 4 public
hosting, authentication, and multiple-worker recovery were outside scope.

Production code and tests were not edited by this reviewer. The implementer made
fixes; independent testers designed and ran the regressions below.

## Must fix — resolved during review

1. **The inference timer cancelled database work** (`backend/inference.py:83`,
   `backend/persistent_chat.py`).
   - **What this is:** An async generator yields an event to its caller, which then
     saves a checkpoint. Both functions run in the same task.
   - **Problem:** The upstream timeout stayed armed across that yield. With an
     immediate `Hello` event, a 50 ms total budget, and a 200 ms checkpoint, it
     cancelled the checkpoint and recorded `cancelled/client_disconnected` without
     a safe terminal error. The client had not disconnected.
   - **Fix:** Keep the absolute upstream deadline around upstream I/O only. Bound
     checkpoints by the remaining generation budget, then record failed state and
     emit `upstream_timeout` when that budget expires. The unchanged independent
     PostgreSQL regression passes and preserves `Hello`.
   - **If we skip it:** Slow persistence makes request history report a user stop
     that never happened and leaves the browser with an incomplete stream.

2. **A delayed list removed the newly selected conversation**
   (`frontend/app.mjs:51`).
   - **What this is:** A terminal refresh reloads the conversation selector while
     the user can start a new conversation.
   - **Problem:** Hold the old list response, create and select `new-1`, then
     release the old response containing only `first`. The old response deleted
     `new-1` from the selector while the page still sent messages to `new-1`.
   - **Fix:** Capture the selection version before loading the list and apply its
     result only while that version is current. The independent tester confirmed
     the regression fails when this guard is removed and passes with it present.
   - **If we skip it:** The visible selection can disagree with the conversation
     receiving the next message.

3. **Terminal database timeouts used the wrong error and repeated their budget**
   (`backend/persistent_chat.py:14`, `backend/conversations.py`).
   - **What this is:** A final database write can fail after the model has already
     finished. Cleanup may retry recording failure and run a disconnect fallback.
   - **Problem:** A completed-write timeout after the generation deadline became
     `upstream_timeout`, although persistence failed. Each cleanup attempt also
     received another five seconds, exceeding the documented shared cleanup bound.
   - **Fix:** A distinct persistence timeout preserves `persistence_error`. One
     `Finalization` object shares a five-second deadline across terminal writes
     and the response fallback. The independent terminal-timeout regression passes;
     an independent PostgreSQL test also confirmed a six-second stalled terminal
     write returns within its 4.5–6.5 second assertion window, keeping the last
     committed checkpoint. A reviewer probe of a shortened shared budget returned
     in 53 ms for 50 ms.
   - **If we skip it:** The client receives the wrong failure reason, and repeated
     database stalls make disconnect/shutdown cleanup take several timeout periods.

## Checked flows and evidence

- Reviewed the migration and matching ORM models, owner-filtered CRUD and request
  history, cookie hashing/settings, pagination, database error responses, and
  completed-only prompt reconstruction.
- Traced atomic reservation, the conversation row lock and unique running-request
  index, checkpoints, final commit ordering, failed/cancelled partial output,
  restart recovery, and cleanup when headers fail before the generator starts.
- Traced retention and its CLI: dry run by default, explicit `--apply`, conversation
  locks before active-request checks, and running-request protection when an
  expired session would otherwise cascade-delete its history.
- Independently reproduced findings 1 and 3 before their fixes and reran the same
  direct stream scenarios afterward. They now emit the correct safe error and
  attempt the correct durable failed state.
- Independent database tester reported three targeted PostgreSQL regressions
  passing in 2.32 s: checkpoint deadline, terminal timeout classification, and
  normal completion with settings/metrics.
- The independent shared-cleanup PostgreSQL regression passed in 6.34 s including
  its isolated schema setup. It verifies `persistence_error`, no `[DONE]`, and
  preservation of `Hello/partial/running` when terminal writes remain unavailable.
- Independent frontend tester reported **48 Node tests passed**, zero failures or
  skips, including the delayed-list regression and its failing guard-removal check.
- `git diff --check` passed during review. Full project lint, ordinary tests,
  database/live suites, and operational setup evidence belong to the parent
  implementation's final verification record.

No unresolved correctness or data-loss finding remains in the reviewed code.
Retaining running rows during a database outage until startup recovery is an
explicit documented limit. Multiple workers require a different recovery design.

Verdict: Ship.

Not checked: graphical browser layout, public exposure, multiple-worker operation,
and a separate reviewer-run real-model benchmark. Existing model bytes, pins, and
historical benchmark data were outside this change.
