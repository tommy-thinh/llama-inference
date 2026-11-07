# Independent Phase 1 review — 2026-10-24

> Historical review of the pre-migration Qwen Phase 1 implementation. Its
> findings and test results do not establish Llama compatibility. The active
> model migration and current verification state are tracked in
> [project status](project-status.md).

The reviewer and tester were separate subagents with fresh contexts. The tester
derived expected behavior from `PROJECT_SPEC.md`, the documentation, and its
request-flow diagrams before inspecting implementation details. The reviewer used
the `ponytail-review` skill and changed no production code or tests. The primary
agent implemented the fixes; the tester owned all test changes.

## Review report

What this change does: Phase 1 builds and verifies CPU-only Qwen3.5-0.8B serving
and records serial benchmark results. The review assumed one local inference
slot, serial benchmarks, and no public deployment.

**Must fix — all findings resolved:**

1. **A different executable passed runtime verification**
   (`inference/setup.py::checked_runtime_version`, `benchmarks/scripts/run.py::run_benchmark`).
   - **What this is:** Setup and benchmarks verify the pinned runtime before
     recording evidence.
   - **Problem:** Checking the source checkout did not check the executable.
     `/usr/bin/true` passed verification while reporting GNU coreutils.
   - **Fix:** Both callers now check the executable's reported commit against the
     pin, with a ten-second timeout. Independent regressions reject unrelated
     executables and wrong commits before writing verification or benchmark evidence.
   - **If we skip it:** Results can claim the pinned runtime while using another build.

2. **Invalid environment settings defeated explicit command arguments**
   (`inference/config.py::ServerConfig.local`).
   - **What this is:** Command arguments override project environment settings.
   - **Problem:** `QWEN_THREADS=invalid` made `--threads 1 --print-command` fail
     because the environment was parsed before overrides were applied.
   - **Fix:** Apply overrides before converting the selected values to integers.
     The tester's public command regression passes without changing its expectation.
   - **If we skip it:** Valid explicit options cannot recover from malformed inherited settings.

3. **A valid zero decode rate crashed chart generation**
   (`benchmarks/scripts/report.py::render_chart`).
   - **What this is:** The report turns measured decode rates into an SVG chart.
   - **Problem:** A one-token completion can report zero subsequent decode steps.
     An all-zero chart divided by zero when calculating its axis.
   - **Fix:** Give the axis a positive minimum while preserving zero measurements.
     The regression checks valid SVG, a zero-width bar, and the `0.00` label.
   - **If we skip it:** A supported workload can complete but fail to produce its chart.

4. **Connection failures left temporary download files**
   (`inference/setup.py::download_model`).
   - **What this is:** Downloads use a temporary file until model verification succeeds.
   - **Problem:** A connection-opening failure happened before cleanup received
     the temporary path. An empty `.part` file remained.
   - **Fix:** Record the path before opening the connection. The same independent
     regression now verifies that no temporary or final model file remains.
   - **If we skip it:** Failed setup attempts accumulate abandoned files.

The reviewer re-read the four fixes and connected callers. Its targeted ordinary
test run passed **29 tests**. The 2026-10-21 performance evidence remains historical
and unchanged; it is not a new performance measurement of these fixes.

Verdict: Ship.

Not checked by the reviewer: live inference. The primary agent handles current
full-suite/live verification, recorded in [project status](project-status.md).

The final primary-agent run subsequently passed **94 tests with real inference**
(13.74 s), with **87 passed and 7 intentional skips** in the ordinary run. A real
one-token benchmark independently reproduced the zero decode rate and generated
a valid chart. The original baseline remained unchanged, and no temporary servers
remained. Actual records are in
[current verification](../benchmarks/results/2026-10-24-independent-review/verification.json).

Both subagents then independently checked the saved current evidence without
editing tests or starting inference again. JUnit counts, source/binary hashes,
summaries, zero-rate SVG, unchanged baseline hash, and process cleanup agreed.
The reviewer's final verdict remained **Ship**; the tester's final conclusion was
**Pass for the documented Phase 1 contracts**.

## How failures were resolved

The tester recorded each failing expectation before the primary agent changed
production code. The tester and reviewer exchanged reproductions and requirements
references; the primary agent agreed on the classification and minimal fix. All
four were implementation defects. The failing assertions were retained and passed
after the production changes; no requirement was weakened to obtain a green suite.

Two tempting claims were explicitly rejected: missing public quotas are a later
phase, and a nonzero shutdown exit code alone does not prove measurements collected
before shutdown are invalid. Neither was reported as a product defect.

See [testing contracts](testing-contracts.md) for the independent test plan,
case-to-requirement mapping, and actual runs.
