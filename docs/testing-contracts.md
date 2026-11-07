# Phase 1 Llama migration test contracts

These checks keep model identity, safe local serving, and benchmark evidence
observable. Ordinary tests use synthetic GGUF headers and mocked network/native
process boundaries; only the opt-in live suite can establish compatibility of
the downloaded model and real llama.cpp process.

## Requirements and coverage

| Contract | Observable check | Tests |
| --- | --- | --- |
| Python environment contract | Environment name, Python minor version, conda-forge-only channels, and pinned HTTPX/pytest/Ruff versions match the project requirements. | `test_conda_environment.py` |
| Pinned artifact/provenance | Lock selects the official upstream model and immutable conversion revision, exact filename/size/SHA, Q4_K_M, license, and Llama template metadata. Wrong checksum, architecture, quantization, tokenizer, template, or missing special tokens are rejected. | `test_artifacts.py` |
| Manual license gate | No-accept setup stops before clone/build/network/file creation; the downloader requires explicit acceptance and publishes only a verified artifact. | `test_setup.py`, `test_artifacts.py` |
| Runtime pin and reuse | Mismatched runtime source/binary is rejected. A verified existing server is reused without rebuilding. Sibling shared libraries are exposed in the child environment without host-wide changes. | `test_setup.py`, `test_config_and_process.py` |
| Local CPU bounds | Stable Llama alias, `127.0.0.1`, zero GPU layers, one slot, configured thread/context/output values, f16 K/V, Jinja template, and inherited llama flags are checked. | `test_config_and_process.py` |
| Owned process lifecycle | Bounded startup, child-exit handling, occupied-port preservation, terminate/wait/kill/reap, and cleanup on exceptions are checked using mocked children. | `test_config_and_process.py`, `conftest.py` |
| Streaming measurements | TTFT starts at first visible text, SSE usage/finish/terminator and runtime timings are validated, and malformed/disconnected streams fail. | `test_benchmark.py` |
| CPU and memory baseline fields | Runner stores process CPU time and percent per request, RSS, model size/quantization, prompt/config/runtime pins, and warm-up labels. CPU percent is normalized to one logical CPU. | `test_benchmark_runner.py`, `test_reports.py` |
| Llama request/template behavior | Live suite checks model listing, arithmetic completion, progressive SSE, invalid-request recovery, Unicode tokenization round-trip, and Llama header/EOT template markers. | `test_inference.py`, gated by `--run-inference` |
| Historical evidence | Curated Qwen raw result remains byte-identical and cannot be relabeled or overwritten by Llama output. | `test_reports.py`, `test_benchmark_runner.py` |

The independent tester derived these requirements from `MIGRATION_TASK.md`,
`PROJECT_SPEC.md`, the project status and Phase 1 docs before inspecting the
implementation. The full historical Qwen test mapping remains in
[qwen-testing-contracts-2026-10-24.md](qwen-testing-contracts-2026-10-24.md).

## Current verification — 2026-11-08

Tommy confirmed Meta license/access acceptance before downloading the pinned model.
Setup and verify-only pass with the real GGUF, including its Jinja BOS expression
and pinned BOS/EOS token IDs. The independent tester added 20 artifact regression cases and eight foreground-launch checks.

| Command in the llama Conda environment | Outcome |
| --- | --- |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed |
| `pytest -ra` (independent ordinary run) | 137 passed, 7 intentional live-inference skips |
| `pytest --run-inference` | 144 passed, 0 skipped, 0 failed; 14.47 seconds |
| `python -m inference.setup --jobs 3 --accept-llama-license` | Passed with the verified local model and reused native build |
| `python -m inference.setup --verify-only` | Passed |

The initial setup rejection was a production defect: it required literal BOS text
in Jinja source, while the real template emits `bos_token`. Size/SHA checks had
already passed. Regression tests preserve checksum, metadata, vocabulary, and
verify-before-publication checks while accepting the supported expression.

The first live attempt failed at socket creation under the execution sandbox;
rerunning with loopback permission resolved that environment restriction. The
next live run exposed a test oracle defect: `/apply-template` deliberately strips
BOS when tokenization will add it. The corrected test verifies exactly one pinned
BOS in the tokenized prompt, plus all role/EOT markers and Unicode round-trip.
No generation assertions were weakened. JUnit evidence is saved locally at
`benchmarks/results/2026-11-08-llama-baseline/tests.xml`.

The foreground launcher regressions reproduced seven failures before the fix:
wrong model size/checksum, wrong source/executable identity, and overridden invalid
settings. The launch boundary now verifies all pins before exec and carries the
selected binary into environment construction. These tests pass unchanged.

## Failure classification during independent test work

The first draft of the migration tests exposed test-only issues: fixtures had
not yet inherited the expanded pinned metadata contract; the expected table
column order omitted CPU utilization; a report fixture omitted newly required
model identity fields; and a historical summary comparison did not account for
the new optional CPU field. A later setup test accidentally mocked the
`check_output()` subprocess used for version checking. These were isolated to
the test fixtures/assertions, reported with their expected behavior and source,
and corrected without weakening product requirements. The final independent
ordinary run passed.
