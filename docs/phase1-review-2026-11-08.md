# Phase 1 independent review — 2026-11-08

Reviewer: separate GPT-6.1-Sol agent, high reasoning, using `ponytail-review`.
Scope: the entire Phase 1 tree before its first commit; one local user and serial requests.

What this change does: Phase 1 serves the pinned Llama 3.2 1B Instruct model
through CPU-only llama.cpp on localhost. It verifies model/runtime identity,
manages temporary servers, and records reproducible streaming benchmarks.

## Must fix — both resolved

1. **Foreground serving accepted another model under the Llama alias**
   (`inference/server.py`, foreground launch).
   The command checked file existence alone, so pointing `LLAMA_MODEL_PATH` at
   historical Qwen weights could launch Qwen under `llama-3.2-1b-instruct`.
   The launcher now verifies the pinned source, executable version, and model
   size/checksum/metadata before execution. Independent rejection tests pass
   unchanged. Without this fix, serving could silently violate model identity.

2. **Valid command arguments could fail because inherited settings were parsed again**
   (`inference/server.py`, foreground environment construction).
   `LLAMA_THREADS=invalid` with `--threads 1` built a valid configuration, then
   environment construction parsed the invalid value again and crashed.
   Environment construction now receives the selected binary directly.
   Independent tests cover malformed and out-of-range inherited settings.
   Without this fix, explicit launch options could not recover from bad inherited
   configuration.

The reviewer rechecked the fixes and all eight foreground regressions passed.
The independent final ordinary suite reports 137 passed and seven intentional
live skips; saved final live JUnit confirms 144 passed with no failures, errors,
or skips. Ruff checks pass.

The completed baseline contains 56 successful requests, seven clean server
exits, and no prefix reuse. Recomputed summaries match. All eleven archived
source hashes match the benchmark record; the documented later source change
affects foreground launch only. Raw/archive hashes, historical Qwen checksum,
final launcher hash, documentation, and process cleanup agree. No llama-server
processes remain.

**Verdict: Ship.**

Not checked: no additional inference workload was run by the reviewer; it
inspected the completed live-test and benchmark evidence.
