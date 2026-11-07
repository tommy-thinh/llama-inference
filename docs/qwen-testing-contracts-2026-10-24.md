# Historical Qwen Phase 1 testing contracts — 2026-10-24

> This mapping and its pass counts apply to the pre-migration Qwen model only.
> Current Llama migration tests and outcomes are documented in
> [testing contracts](testing-contracts.md).

The independent test pass on 2026-10-24 derived these observable requirements
from the spec and teaching documents before reading implementation details.
Existing useful checks were retained. Changes stay within local, CPU-only,
serial Phase 1 serving; no new dependencies were added.

## Requirements and coverage

| Contract and source | Observable check | Tests |
| --- | --- | --- |
| Exact model and pinned runtime: [spec, Phase 1](../PROJECT_SPEC.md#phase-1--inference-foundation); [setup and compatibility](02-running-llama.md#selected-model-and-verification-status) | Reject wrong size/checksum, architecture, quantization, block/MTP layout, embedding size, tokenizer or template. Setup and benchmark reject a different executable commit even when the source checkout is valid. | `test_artifacts.py`, `test_setup.py`, `test_benchmark_runner.py::test_benchmark_rejects_the_wrong_executable_before_running_profiles` |
| Non-destructive setup: [running guide](02-running-llama.md#verify-the-existing-runtime-without-model-access) | Existing invalid models survive; invalid bytes and connection/read failures publish no final model and leave no temporary download. A verified fixture downloads from the pinned URL before publication. | `test_artifacts.py` |
| Local CPU resource bounds: [launcher settings](02-running-llama.md#start-and-configure-the-server) | Command binds `127.0.0.1`, requests zero GPU layers and one slot, preserves documented thread/context/cache settings, filters inherited llama flags, rejects unsafe numeric settings, and lets explicit flags override environment values without starting a server in print mode. | `test_config_and_process.py` |
| Owned process lifecycle: [process ownership](02-running-llama.md#tests-and-process-ownership); [spec testing standards](../PROJECT_SPEC.md#6-testing-standards) | Recover from transient unready health; fail on bounded startup timeout, child exit, missing assets and spawn failure; preserve an occupied port/service; terminate, wait, kill/reap when necessary and close logs. Linux memory is recorded in bytes and an exited process is handled. | `test_config_and_process.py`; teardown assertion in `conftest.py` |
| Streaming and measurements: [clock definitions](01-inference-fundamentals.md#what-the-clocks-measure); [measurement limits](03-qwen-historical-benchmark.md#measurement-definitions-and-limits) | TTFT begins at the first visible delta, excluding heartbeat/role/empty deltas. Preserve runtime counts independently of content/network chunks, handle fragmented UTF-8 and separate usage events, and reject missing text/finish/usage/terminator, malformed/error SSE, invalid counts and HTTP errors. Midstream disconnects/timeouts propagate and close the response. | `test_benchmark.py` |
| Reproducible serial benchmark: [evidence/reproduction](03-qwen-historical-benchmark.md#preserved-evidence); [method](03-qwen-historical-benchmark.md#method-change-one-factor-at-a-time) | Use one owned profile at a time; persist settings, prompts, hashes, hardware, startup, memory and exit evidence. Keep warm-ups but exclude them from per-prompt summaries. Refuse prior output, retain partial results and mark failed startup/disconnection runs as failed. | `test_benchmark_runner.py`, summary/config checks in `test_benchmark.py` |
| Honest reporting: [results and definitions](03-qwen-historical-benchmark.md#results) | Preserve measured values and valid SVG, refuse failed experiments, maintain consistency of curated raw evidence, and plot a legitimate zero decode rate as zero. | `test_reports.py` |
| Actual inference acceptance: [spec completion criteria](../PROJECT_SPEC.md#completion-criteria); [API usage](02-running-llama.md#call-the-local-api) | Verify real assets, startup/health/model alias, arithmetic with natural termination, progressive streaming and reported cache use, malformed requests followed by recovery, Unicode tokenizer round-trip/chat template, and real benchmark evidence. | `test_inference.py`, gated by `--run-inference` |

The runner fixture replaces model verification, managed native processes and OS
metadata with controlled values. It exercises the real runner, HTTPX, SSE parser,
measurements, summary and file writer together. Protocol tests tie a deterministic
clock to delivered network events, so an incorrect role-event TTFT would fail.

## Recorded results

These are historical results from the former project virtual environment. The
2026-10-21 curated inference/benchmark evidence was preserved. Current commands
use the `llama` Conda environment; see [current testing contracts](testing-contracts.md).

| Point in this pass | Command | Actual result |
| --- | --- | --- |
| Before test edits or production fixes | `pytest -ra` | 40 passed, 7 intentional inference skips, 0 failed; 0.21 s |
| After test additions and agreed fixes | `ruff check .` | Passed |
| After test additions and agreed fixes | `ruff format --check .` | Passed; 27 files already formatted |
| After test additions and agreed fixes | `pytest -ra` | 87 passed, 7 intentional inference skips, 0 failed; 0.44 s |
| Missing configured runtime/model | `env LLAMA_SERVER_PATH=/tmp/qwen-contract-missing-server QWEN_MODEL_PATH=/tmp/qwen-contract-missing-model.gguf pytest -q` | 87 passed, 7 intentional inference skips, 0 failed; 0.33 s |
| Final primary-agent ordinary check | `pytest --junitxml=benchmarks/results/2026-10-24-independent-review/ordinary-tests.xml` | 87 passed, 7 intentional inference skips, 0 failed; 0.48 s |
| Final primary-agent live check | `pytest --run-inference --junitxml=benchmarks/results/2026-10-24-independent-review/live-tests.xml` | 94 passed, 0 skipped, 0 failed; 13.74 s |
| Final primary-agent one-token check | Real benchmark runner with `max_tokens=1`, one warm-up and one measured request, followed by report generation | Both requests returned visible text and exactly 0.0 runtime decode tokens/s; valid SVG generated |

No real inference was started by the independent tester. The primary agent ran
the full opt-in suite separately to avoid competing CPU workloads. Actual current
command outputs, JUnit records, process cleanup, unchanged historical-baseline hash,
and the one-token result are preserved in
[verification.json](../benchmarks/results/2026-10-24-independent-review/verification.json).
Use `pytest --run-inference` after setup in an activated environment; missing assets
fail that run.

## Failures found before production fixes

Each failure was sent to the implementation author and independent reviewer
before its production fix. They agreed these four cases violated Phase 1
contracts. The tests were retained through the fixes.

| Regression command | Original failure | Expected behavior |
| --- | --- | --- |
| `pytest -q tests/test_artifacts.py::test_connection_failure_leaves_no_partial_download` | 1 failed in 0.05 s: the empty-directory assertion found a leftover `.part` after `URLError`. | Preserve no final model; clean the temporary download after connection failure. |
| `pytest -q tests/test_setup.py --tb=short` | 2 failed in 0.07 s: `DID NOT RAISE ValueError`; setup claimed verification for unrelated executable output and for a different llama commit. | Reject mismatched executable provenance before writing a verified report. |
| `pytest -q tests/test_config_and_process.py::test_print_command_overrides_invalid_environment_without_starting --tb=short` | 1 failed in 0.06 s: `ValueError: invalid literal for int() with base 10: 'invalid'`. | Explicit valid CLI flags override their malformed environment counterparts. |
| `pytest -q tests/test_reports.py::test_chart_preserves_a_valid_zero_decode_measurement --tb=short` | 1 failed in 0.06 s: `ZeroDivisionError` calculating the chart axis. | Produce valid SVG with a zero-width bar and `0.00` label. |

## Oracle decisions and limits

Missing runtime decode timing is allowed by the existing unavailable-rate report
path; tests do not invent a rate or require that optional field in every stream.
A one-token generation can legitimately report zero decode steps/rate, so zero
must remain representable. A `length` finish is valid completion at an output cap;
the separate arithmetic test verifies natural `stop` termination.

Service unavailability and resource failures use deterministic missing files,
HTTP errors, startup deadlines, occupied ports and interrupted downloads/streams.
No deliberate OOM, simultaneous inference load, public listener, Phase 2 client
cancellation, queue/admission controls, or quality/performance thresholds were
introduced. Mocked checks establish control-flow contracts; only an opt-in live
run can establish compatibility of the actual native runtime and model.
