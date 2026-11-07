# Project status

Updated: 2026-11-08. **Llama is downloaded, verified, and passes all live tests.
The complete 56-request Phase 1 baseline is recorded. Tommy has authorized Phase 2 after the
Phase 1 review/audit and commit.**

## Llama setup and verification — 2026-11-08

- Tommy explicitly confirmed personal acceptance of the model license/access
  terms before acquisition. The exact pinned Bartowski Q4_K_M GGUF is stored in
  `.runtime/models/Llama-3.2-1B-Instruct-Q4_K_M.gguf` (807,694,464 bytes, SHA-256
  `6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83`).
- The existing pinned CPU llama.cpp binary was reused without rebuilding. Setup
  and `--verify-only` both pass; `.runtime/artifact-verification.json` records
  the real metadata and runtime commit.
- Fixed an overly strict source-template check: the real template starts with
  `{{- bos_token }}`. The verifier accepts that output expression and now also
  pins BOS ID 128000 and EOS ID 128009; exact size/SHA and other metadata,
  template, and vocabulary requirements remain enforced.
- Independent regressions classified this as a production validation defect.
  A later live test oracle incorrectly expected BOS in `/apply-template` text;
  this runtime strips it before tokenization adds it. The test now verifies one
  BOS in the effective token sequence and retains all other marker checks.
- Ordinary independent suite: **137 passed, 7 intentional skips**. Full suite
  with `pytest --run-inference`: **144 passed, 0 skipped, 0 failed in 14.47 s**.
  Real startup, arithmetic, streaming, malformed-request recovery, Unicode,
  template/tokenization, and benchmark smoke all pass. Ruff lint and format
  checks pass. Initial socket errors were sandbox restrictions;
  the successful live suite ran with temporary loopback sockets permitted.
- Review found and independent tests reproduced two foreground launcher defects:
  re-reading malformed environment after explicit CLI overrides, and launching
  unverified weights under the Llama alias. Foreground launch now verifies the
  pinned source/executable/model and uses the selected binary environment.
- The complete baseline has 56 successful requests, 14 warm-ups, 42 measured
  samples, seven clean server exits, and no prefix reuse. Four-thread short/long
  TTFT is 0.491/2.412 seconds; decode speed is 19.76/17.83 tokens/s; maximum RSS
  across the sweep is 1430.7 MiB. See [the benchmark guide](03-baseline-benchmark.md).
- Curated raw results, report, chart, exact source snapshot, JUnit, and verification
  are in `benchmarks/results/2026-11-08-llama-baseline/`. The snapshot precedes
  the foreground-only launcher fix; the verification record explains the delta.
  Final process check found no remaining llama-server processes.

Independent GPT-6.1-Sol agents at high reasoning completed the
[review](phase1-review-2026-11-08.md) and [audit](phase1-audit-2026-11-08.md).
The review's two launch findings were fixed and retested; final verdicts are
**Ship** and **Healthy for Phase 1**. Phase 1 is ready for its requested commit.

## Python environment migration — 2026-11-02

- Miniconda 26.9.4 was already installed. The requested `llama` environment was
  created under `/root/miniconda3/envs/llama` with Python 3.11.18.
- `environment.yml` uses only conda-forge and pins Python 3.11, HTTPX 0.28.1,
  pytest 9.1.1, and Ruff 0.17.1. All direct packages are available from Conda;
  no pip-only dependency was needed. These match the direct dependency versions
  from the former Python lockfile.
- The initial default-channel creation and later `conda env create/update`
  attempts were stopped by Anaconda's unaccepted default-channel terms. No terms
  were accepted. The requested environment was created and its dependencies were
  installed with explicit conda-forge-only channel overrides. `environment.yml`
  is provided for reproducibility; its solver path could not be completed under
  this host's global ToS check.
- Benchmark evidence now hashes `environment.yml` and records the Conda name,
  prefix, and Python prefix without requiring a separate package-manager command.
- The former Python lockfile, virtual environment, and package cache were removed
  only after the Conda environment installed the dependencies and passed tests.
  The llama.cpp checkout/build, model files, and curated benchmark evidence were
  preserved.

| Command in the `llama` environment | Actual result |
| --- | --- |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed; 33 files already formatted |
| `pytest -ra` | 109 passed, 7 intentional live-inference skips, 0 failed |
| `python -m inference.setup --verify-runtime-only` | Pinned source and executable verified at commit `620d2225cc436ab0b001e3200e590e3b286ef9ab` |
| `python -m inference.server --help` | Passed |
| `python -m benchmarks.scripts.run --help` | Passed |
| `pytest --run-inference` | Not run: no accepted access or downloaded Llama artifact |

Tests ran through `conda run -n llama`; temporary cache paths were supplied only
to avoid writing to the sandbox's read-only home cache. The seven live-inference
cases remain skipped by the ordinary suite and cannot verify Llama generation.

## Llama inference status as of 2026-10-25

- Active configuration selects Meta Llama 3.2 1B Instruct, GGUF Q4_K_M, from a
  pinned Bartowski conversion revision with expected filename, size, SHA-256,
  and metadata checks in `inference/artifacts.lock.json`.
- The official model page is manually gated and uses Meta's Llama 3.2 Community
  License. This workspace has not accepted terms or downloaded Llama weights.
  Setup now requires an explicit `--accept-llama-license` attestation before
  cloning/building/downloading.
- The existing CPU llama.cpp checkout/build remains pinned. Its local shared
  library directory is added to subprocess `LD_LIBRARY_PATH`, allowing version
  and startup checks without a system-wide install.
- The benchmark now captures each request's server-process user+system CPU time
  as a percentage of one logical CPU, alongside timing/RSS evidence. There are
  no Llama measurements yet.
- Existing Qwen weights and curated results remain intact as historical data.
  The Qwen model SHA-256 remains `fb044e93939a70469c905781334f5de1e6c8b608ced6cbc8c9249bd4127d9526`;
  the original raw benchmark SHA-256 remains `3f70f3b79ff8270511b52eaa0ae4f0907adfe14af9d7151abfb397e116f7a3fe`.
  Neither is Llama evidence.
- Independent migration tests completed. The primary-agent lint/test rerun also
  passed, but live tests and the Llama baseline await accepted access and a
  verified local artifact.
- The six-phase roadmap is preserved; current authorization remains Phase 1.

The pre-Conda ordinary run had 107 passes, seven intentional live-inference
skips, and no failures. Runtime verification passed; setup correctly refused to
download before manual model acceptance. There are still no Llama measurements.

After personally accepting the model terms, activate `llama` and use:

```bash
python -m inference.setup --jobs 3 --accept-llama-license
python -m inference.setup --verify-only
pytest --run-inference
```

Update this status after ordinary verification and again when real Llama
inference and benchmark evidence exist.

## Repository location and Git — 2026-10-25

Tommy renamed the project directory from `/root/qwen` to `/root/llama`. This is
still the same Phase 1 project. The existing Git repository on `main` was preserved;
no commit or remote was created by this setup task.

At that time, ignore rules covered downloaded runtimes, environments/caches,
native and packaging builds, model weights, incomplete downloads, local secrets,
logs, and uncurated benchmark runs. The active ignore rules and dependency files
have since changed during the Conda migration. The `PROJECT_SPEC.md` exception
is preserved.

An independent tester checked 43 ignored paths and 31 eligible paths in an isolated
Git repository. Checks against the real repository also passed; all eligible files
total less than 1 MiB. No model data, builds, caches, or history were deleted.

This chat was resumed in place through Codex's supported local API. The returned
chat ID is unchanged, and both its saved/effective working directory and runtime
workspace root were verified as `/root/llama`. Historical benchmark records retain
the paths captured when those experiments ran.

## Historical Qwen review and verification — 2026-10-24

> The results in this section describe the pre-migration Qwen configuration.
> They verify the old model/runtime only and are not Llama test or performance
> results.

Tommy requested a separate `ponytail-review` reviewer and an independent tester.
Both start from fresh context. The tester derives expectations from the spec and
documentation, then rewrites tests where this improves coverage of observable
behavior. Production fixes, if required, stay with the implementation agent.
Failures will be classified and discussed before tests or code are changed.

The reviewer and tester reproduced four implementation defects: accepting an
unrelated executable as the pinned runtime, parsing environment settings before
explicit overrides, dividing by zero for a valid zero-speed chart, and leaving a
temporary download file when connection setup fails. All four minimal production
fixes are applied, and the original failing regressions pass unchanged. The
reviewer's targeted run passed 29 tests and its final verdict is **Ship**. See the
[full independent review](independent-review.md) and the tester's
[requirements-to-tests mapping](testing-contracts.md).

The tester retained useful checks and added independent model/setup, process,
streaming, and runner coverage. Tests now total 94 cases. The ordinary suite also
passed with configured model/runtime paths pointing to nonexistent files.

| Current command | Actual result |
| --- | --- |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed |
| `pytest` | 87 passed, 7 intentional real-inference skips, 0 failed (0.48 s) |
| `python -m inference.setup --verify-only` | Pinned source/executable and model metadata/checksum verified |
| `pytest --run-inference` | 94 passed, 0 skipped, 0 failed (13.74 s) |
| Real one-token benchmark and chart | 1 warm-up + 1 measured request; both report exactly 0.0 decode tokens/s; valid SVG generated |

These checks used the former project virtual environment in the agent's tool
shell. The full historical run
verifies real generation, progressive streaming, invalid requests followed by
recovery, Unicode tokenizer round-trip, the chat template, and benchmark smoke.

The final process check found no newly remaining inference processes; there were
no pre-existing llama-server processes in this run. The original baseline file's
SHA-256 is unchanged. No full performance sweep was rerun and no Phase 2 work began.
Both independent subagents subsequently checked the saved current evidence. The
reviewer confirmed **Ship** and the tester confirmed **Pass for Phase 1**.

Current evidence:

- [Final command outputs, suites, cleanup and baseline check](../benchmarks/results/2026-10-24-independent-review/verification.json)
- [Ordinary suite JUnit](../benchmarks/results/2026-10-24-independent-review/ordinary-tests.xml)
- [Live suite JUnit](../benchmarks/results/2026-10-24-independent-review/live-tests.xml)
- [Real one-token raw result](../benchmarks/results/2026-10-24-independent-review/one-token.json)
- [Real zero-rate SVG](../benchmarks/results/2026-10-24-independent-review/one-token-chart/decode-speed.svg)

The results below are the original Qwen verification, retained as historical
evidence. Current Llama verification is recorded at the start of this document.

## Historical Qwen Phase 1 milestone — completed before model migration

- [x] Inspected repository, OS, CPU features, compiler, memory, disk, and installed tools.
  VPS: Ubuntu 26.04.1, six Xeon Platinum 8272CL vCPUs, approximately 16 GB RAM,
  no swap, approximately 80 GB free disk before setup. Python 3.14.4,
  GCC 15.2, CMake 4.2.3, and Make were already installed.
- [x] Built pinned llama.cpp v0.7.0, commit
  `620d2225cc436ab0b001e3200e590e3b286ef9ab`, with native CPU support and three
  build jobs. Re-ran setup successfully to check repeatability. No system packages
  or services were installed or reconfigured.
- [x] Verified the exact model architecture, hybrid state support, tokenizer, chat
  template, and Q4_K_M availability against upstream source and the real GGUF.
  Artifact has 24 text layers plus an optional MTP head; MTP decoding stays disabled.
- [x] Downloaded Bartowski's post-trained Qwen3.5-0.8B Q4_K_M conversion at
  `f36b1ea49a332ede8fe5f389bbf5b3575ef71f48`. Verified 579,615,840 bytes and SHA-256
  `fb044e93939a70469c905781334f5de1e6c8b608ced6cbc8c9249bd4127d9526`.
- [x] Verified local health/model endpoints, arithmetic answer `4`, tokenization,
  template rendering, progressive SSE generation, natural termination, and HTTP
  400 for an invalid request. Inference is CPU-only and binds to `127.0.0.1`.
- [x] Completed a real seven-profile benchmark: 14 warm-ups and 42 measured
  requests, with 2/4/6 threads, 1K/2K/4K context capacity, and 32/64/128 output caps.
  All requests succeeded; all seven owned servers exited with code 0.
- [x] Added meaningful automated tests, required teaching documentation, an ADR,
  reproducible setup commands, raw benchmark evidence, tables, and an SVG chart.

## Original Qwen verification — 2026-10-21

| Command | Actual result |
| --- | --- |
| Historical dependency setup | Passed; recorded environment matched its dependency lock |
| `python -m inference.setup --verify-only` | Runtime, checksum and selected GGUF metadata verified |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed |
| `pytest` | 40 passed, 7 intentional real-inference skips, 0 failed (0.13 s) |
| `pytest --run-inference --junitxml=benchmarks/results/2026-10-21-baseline/tests.xml` | 47 passed, 0 skipped, 0 failed (10.79 s) |

The final host process check found **no remaining llama-server processes**.
Manual serving is a foreground command; test/benchmark servers are temporary.

The four-thread 64-output-token baseline measured short/long median TTFT of
0.318/1.990 seconds and mean runtime decode speed of 19.85/19.60 tokens/s. Six
threads were fastest tested at 23.62/23.90 tokens/s. Peak RSS across all profiles
was 874.5 MiB. These are small serial experiments, not general quality or capacity
claims. See the methodology and limitations before interpreting them.

Evidence:

- [Final command outputs and verification record](../benchmarks/results/2026-10-21-baseline/verification.json)
- [JUnit results](../benchmarks/results/2026-10-21-baseline/tests.xml)
- [Raw benchmark](../benchmarks/results/2026-10-21-baseline/raw.json)
- [Summary table](../benchmarks/results/2026-10-21-baseline/summary.md)
- [Decode-speed chart](../benchmarks/results/2026-10-21-baseline/decode-speed.svg)

## Documentation and important files

- [Inference fundamentals and learning checkpoint](01-inference-fundamentals.md)
- [Running Llama, license/access gate, configuration and troubleshooting](02-running-llama.md)
- [Llama baseline status and measurements](03-baseline-benchmark.md)
- [Historical Qwen baseline](03-qwen-historical-benchmark.md)
- [Current Llama model/runtime ADR](decisions/0002-llama-model-migration.md)
- [Historical Qwen runtime ADR](decisions/0001-phase1-cpu-runtime.md)
- `inference/`: pinned assets, verification/setup, configuration and server lifecycle.
- `benchmarks/`: fixed workloads, streaming metrics, runner and report generator.
- `tests/`: ordinary unit/protocol tests and opt-in real inference tests.
- `README.md`, `AGENTS.md`, `.env.example`, `pyproject.toml`, `environment.yml`.

Weights, builds, logs, environments and transient results are ignored by Git.
The selected original benchmark and verification artifacts are intentionally curated.

## Remaining limitations

Phase 1 implementation, live tests, and baseline are complete; independent
review/audit are complete, with the requested commit next. The suite establishes local functional behavior,
not general answer quality, concurrent capacity, or public-serving safety.
Historical Qwen measurements remain specific to their original model and runtime.

## Next approved action

Finish independent review and audit; address concrete
findings and commit with message `Phase 1`. Tommy explicitly authorized Phase 2
on 2026-11-08 after that commit. Implement only its local FastAPI/vanilla-JavaScript
scope, then stop before Phase 3. Public deployment remains a later phase.
