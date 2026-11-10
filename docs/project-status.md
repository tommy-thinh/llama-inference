# Project status

Updated: 2026-11-09. **Phase 4 code and deployment preparation are ready locally;
public deployment is explicitly deferred by Tommy.** Phases 1–3 are committed as
`de5b6b5`, `e9afd64`, and `bbae986`. No Phase 5 work is authorized.

## Phase 4 — prepared, not deployed

- Public mode is opt-in through an exact HTTPS `LLAMA_PUBLIC_ORIGIN`, database URL,
  and Secure cookies. The local development profile remains available. No domain,
  firewall, public listener, installed service, ACME certificate, or application
  migration was configured by this milestone. Migration `0002` is prepared and
  tested only in isolated test schemas; the application DB remains at `0001`.
- Added same-origin/Host/Fetch Metadata guards, JSON/64KiB request bounds, security
  headers, bounded rate tracking, safe JSON request/stream logs, and dependency
  readiness. Public transient chat and API documentation are disabled. Client IP
  is trusted only from the loopback Caddy peer's overwritten `X-Real-IP`.
- Default public limits: 60 API requests/IP/minute, 6 inference requests/session/
  minute; durable daily 50 requests/32768 reserved output tokens per session and
  200/131072 per IP. Reservations include cancelled/failed/queue-rejected turns
  once the turn transaction commits; unused actual token counts are not refunded.
- One active generation and two FIFO waiters, ten-second queue wait, explicit
  429/503 with Retry-After. Cancellation, upstream closure, terminal persistence,
  and permit release are independently tested. Daily reservations survive restart;
  minute windows/queue are intentionally single-process and reset on restart.
- Prepared Caddy static allowlist/reverse proxy, three private environment
  examples, dedicated nonroot systemd units/resource ceilings, least-privilege
  runtime grants, checked private atomic logical backups, and manual fresh-target
  restore/rollback. These files are not installed. Complete official Meta license
  and use policy are pinned with source hashes; UI displays Built with Llama.
- Independent tests first derived requirements from the spec/docs. Review found
  and fixed a request-count normal shutdown incompatible with restart-on-failure,
  and readiness falsely passing without the quota schema. Testing also found a
  permit promotion/cancellation race; the unchanged regression passes after the
  fix. Other failure classifications are recorded in the verification evidence.
- A checksum-verified Ubuntu Caddy 2.6.2 package was extracted under ignored
  `.runtime` strictly for testing. It was not installed. Local HTTP proxy tests
  verify real assets/imports, private-path isolation, client-header overwriting,
  progressive SSE and cancellation. A custom-format PostgreSQL dump restored
  successfully only within an isolated `_test` schema.
- A complete certificate-verified **loopback HTTPS** check connected actual Caddy,
  a real backend process, PostgreSQL, and pinned Llama inference. It verified
  Secure cookies, arithmetic streaming/metrics, durable quotas, saved history
  after process restart, cross-origin403, transient404, cancelled partial text,
  and a successful following request. The temporary self-signed certificate was
  trusted only by that test client; no system CA change or ACME request occurred.

| Executed in the `llama` environment | Actual result |
| --- | --- |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed; 77 files formatted |
| `pytest -q` | **318 passed, 51 intentional skips**, 10.24 s |
| `pytest -q --run-database --run-inference` | **369 passed, 0 skipped, 0 failed**, 59.18 s |
| Independent security/public persistence tests | 53 passed plus new readiness migration regression passed |
| Independent deployment tests | 12 passed, including real isolated dump/restore |
| Independent real public-stack HTTPS test | 1 passed, 7.40 s; also included in final full suite |

The ordinary skips are 43 database cases (two also need native inference) and
8 other native cases. Caddy tests ran using the staged verified binary. A machine
without a test Caddy binary reports additional explicit skips. The first parent
ordinary run could not create loopback/systemd-analysis sockets inside the
restricted sandbox; the unchanged final suite ran with approved temporary socket
access. An initial public-stack test parser incorrectly joined valid null-content
role events; the tester corrected that oracle and retained all behavior checks.

Evidence: [final verification/source snapshot](evidence/phase4-verification.json),
[ordinary JUnit](evidence/phase4-ordinary-tests.xml),
[full live JUnit](evidence/phase4-live-tests.xml),
[proxy/backup/unit checks](evidence/phase4-deployment-verification.json),
[real loopback HTTPS stack](evidence/phase4-public-stack-verification.json),
[read-only not-deployed check](evidence/phase4-not-deployed.json).
Final inspection found no remaining owned Caddy/native processes or listeners
on80/443/2019/8000/8081, no installed Phase4 unit files, and PostgreSQL still only
on127.0.0.1:5432. The application migration remains0001, with no quota table.
Phase4 changes remain uncommitted for Tommy's review.

Read [deployment instructions](09-deployment-guide.md),
[security/resource model](10-security-and-resource-management.md), and
[operations/recovery](11-operations-and-troubleshooting.md). Independent
[review](phase4-review-2026-11-09.md) verdict: **Ship the local preparation**.
Actual public HTTPS, DNS/firewall checks, installed-service restart/recovery, and
production restore remain deployment-time checks. The Phase 4 public-access
completion criterion is pending by explicit user instruction.

## Phase 3 — PostgreSQL and persistence

- Installed PostgreSQL **18.6** as a new `18/main` cluster. It listens only on
  `127.0.0.1:5432`, with SCRAM password authentication, UTC, and 30 connections.
  Dedicated application/test databases have separate nonsuperuser roles with no
  database/role creation privileges. Private generated credentials are in ignored
  `.runtime/database.env`, mode 0600. No prior cluster was replaced.
- Pinned SQLAlchemy **2.0.54**, Alembic **1.20.1**, and psycopg **3.3.8** in
  `environment.yml`. Existing inference runtime, exact Llama weights, architecture,
  historical Qwen artifacts, and baseline measurements are preserved.
- Alembic revision `0001` creates anonymous sessions, conversations, messages, and
  inference requests/metrics, with ownership keys, state checks, indexes, and one
  running request per conversation. Application schema upgrade and `alembic check`
  both pass; the model and migration definitions have no pending drift.
- Browser bootstrap restores cookie-owned conversations; New Conversation and
  selection use the persistent API. Random 256-bit cookies are HttpOnly and
  SameSite=Lax; only hashes are stored. Expiry is fixed at 30 days. Completed
  persisted turns supply model context; cancelled/failed output stays visible
  but is excluded from subsequent model input. `/api/chat` remains an explicit
  transient compatibility endpoint.
- Short transactions reserve turns, checkpoint partial text, and commit terminal
  state before success. Inference errors/timeouts become failed, disconnects
  cancelled, and startup recovers interrupted running requests without losing
  committed text. All finalization attempts share a bounded, shielded five-second
  budget. A database outage preserves the last durable checkpoint and reports a
  safe error instead of successful completion.
- Basic retention is manual: `python -m backend.retention --days 30` previews,
  `--apply` deletes eligible rows. Locks protect running conversations and their
  owners. Application dry run found zero eligible conversations/sessions and
  deleted nothing. Apply behavior is tested only in isolated test schemas.
- Independent testers derived requirements from the spec and database/session
  docs before implementation review. They covered migrations up/down, constraints,
  CRUD/pagination/ownership, rollback, errors/partial states, early disconnects,
  real PostgreSQL write stalls, retention, metrics, and actual Llama restoration.
  Their frontend regressions cover failed selection and stale asynchronous replies.

| Executed in the `llama` Conda environment | Actual result |
| --- | --- |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed; 64 files formatted |
| `pytest -q` | **268 passed, 34 intentional skips**, 5.31 s |
| `pytest -q --run-database --run-inference` | **302 passed, 0 skipped, 0 failed**, 37.86 s |
| `alembic upgrade head` and `alembic check` | Revision `0001`; no pending upgrade operations |
| `python -m backend.retention --days 30` | Dry run; zero eligible rows; `applied=false` |
| Independent Node behavior suite | **48 passed**, no failures/skips |
| Real TCP persistence smoke | Passed: page/assets, generation/metrics, restart restore, owner isolation, cancelled partial |

The ordinary skips are 26 PostgreSQL cases (one also needs the model) plus eight
existing model cases. Explicit live tests use `LLAMA_TEST_DATABASE_URL`, refuse a
database name without `_test`, and create/drop only unique temporary schemas.
Neither the integration suite nor TCP smoke uses application conversation data.

The actual TCP check generated arithmetic answer `4`, saved 49 prompt/2 completion
tokens, and recorded backend TTFT 0.470 s, terminal latency 0.525 s, and native
decode 18.29 tokens/s. These describe **one functional smoke request**, not a new
controlled inference benchmark. It restored identical messages after a real
backend process restart, denied another cookie owner, then disconnected after
three visible chunks and retrieved the saved cancelled partial text. Both owned
backend/native processes were reaped and the test schema removed. PostgreSQL
remains running as the intended local database service.

The independent GPT-6.1-Sol high [review](phase3-review-2026-11-09.md) verdict is
**Ship**. Review findings were fixed and independently reproduced/retested: a
shared-task inference timer cancelling checkpoint work, a stale conversation list
overwriting a new selection, and terminal DB timeout classification/cleanup
budget. A separate frontend selection defect was also fixed. Initial stale
manifest/bootstrap assertions were classified as test defects and corrected;
behavior regressions were retained.

Evidence: [verification/source snapshot](evidence/phase3-verification.json),
[ordinary JUnit](evidence/phase3-ordinary-tests.xml),
[full live JUnit](evidence/phase3-live-tests.xml),
[database setup](evidence/phase3-database-setup.json),
[TCP smoke](evidence/phase3-local-smoke.json).

Read [database design and local setup](07-database-design.md) and
[sessions/persistent API](08-sessions-and-persistence.md) to understand the new
layer. Tommy subsequently committed Phase 3 as `bbae986` (`Phase 3`).
The results above describe that snapshot before Phase 4 preparation.


## Output-limit update

- Tommy requested a default output budget of **1,024 tokens** and a maximum of
  **2,048 tokens**. API validation, UI defaults/bounds, native configuration,
  environment example, and current usage docs now agree.
- Native default context is **4,096 tokens**, leaving prompt room alongside the
  maximum output budget. Explicit output must remain below configured context.
- Independent affected tests passed, including inclusive 2,048 and rejected
  2,049 boundaries, forwarding, HTML defaults, and native context constraints.
- Full ordinary suite: **265 passed, 8 intentional live skips**. Full live suite:
  **273 passed, 0 skipped, 0 failed (18.40 s)**. Ruff lint and formatting pass.
- [Verification](evidence/output-limits-verification.json) and
  [live JUnit](evidence/output-limits-live-tests.xml) record this update. Earlier
  Phase 1/Phase 2 measurements, source hashes, and test counts below describe
  their original snapshots before this policy change.

## Phase 2 — local backend and frontend

- Added FastAPI with a per-application HTTPX AsyncClient, bounded Pydantic input,
  SSE relay, connect/read/total deadlines, safe errors, and explicit cancellation
  cleanup for ASGI 2.3 and 2.4. It uses the separate existing model process.
- Added a responsive vanilla JavaScript page with progressive text, bounded
  generation settings, loading/error states, Stop Generation, safe text rendering,
  and in-tab conversation history. Failed/stopped turns are not sent as history.
- Conda pins FastAPI core 0.162.2, Uvicorn 0.54.0, Pydantic 2.14.3, AnyIO 4.16.0,
  and Node 24.22.1. Existing Python/HTTPX/pytest/Ruff pins remain. Node is solely
  for dependency-free JavaScript tests; no Node application server is used.
- Independent backend suite: 100 passed, plus 14 deadline/ownership regressions. Independent frontend suite: 33 Node
  behavioral cases passed; its pytest bridge passes. Combined ordinary suite:
  **252 passed, 8 intentional live-test skips**. Full `pytest --run-inference`:
  **260 passed, 0 skipped, 0 failed (19.17 s)**.
- Real TCP smoke: static page/assets served; Node Fetch and the actual frontend
  parser received progressive Llama text, cancelled after two chunks, and
  recovered with arithmetic answer `4` in about 0.48 s. The native log confirms
  cancellation. Uvicorn and llama-server were terminated/reaped afterward.
- The audit found terminal error delivery could block before upstream cleanup.
  It is fixed: every upstream error closes its response before delivery, and all
  downstream sends have a total-deadline bound with at most 100 ms terminal grace.
  Fourteen independent raw-ASGI regressions failed before this fix and now pass.
- [Saved verification](evidence/phase2-verification.json),
  [live-suite JUnit](evidence/phase2-live-tests.xml), and
  [TCP smoke](evidence/phase2-local-smoke.json) record final actual outcomes.
- Documentation: [architecture](04-fastapi-architecture.md),
  [HTTP streaming](05-http-streaming.md),
  [frontend communication](06-frontend-backend-communication.md).
- Automated browser rendering/visual layout was not checked. Frontend behavior
  uses a minimal DOM fixture; the TCP smoke uses real Fetch and SSE parsing.

Independent GPT-6.1-Sol agents at high reasoning completed the Phase 2
[review](phase2-review-2026-11-08.md) and [audit](phase2-audit-2026-11-08.md).
Final verdicts are **Ship** and **Healthy** after the audit finding was fixed and
retested. Tommy subsequently committed Phase 2 as `e9afd64` (`Phase 2`). The
`Phase 1` commit remains `de5b6b5`.

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
**Ship** and **Healthy for Phase 1**. Phase 1 was committed as `de5b6b5`.

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

Phase 3 is a local single-worker application with anonymous cookie ownership,
not account recovery. Clearing the cookie loses access. Recovery runs on startup;
multiple workers or overlapping instances need a lease design first. A crash or
DB outage may lose text since the last checkpoint. The UI lists the latest 20
conversations and latest 100 messages; older pages are available through the API
without UI paging controls. Public HTTPS remains deferred. CSRF/rate/quota/admission controls and
backup/recovery procedures are prepared in Phase 4; live enforcement by the
installed deployment remains unverified. Prompt character
bounds do not replace runtime token checks. Graphical browser layout was not
automated. Phase 1 baseline measurements remain separate historical evidence.

## Next approved action

Tommy can review Phase 4 preparation and follow docs09 when he chooses to
deploy and supplies a real domain. **Do not deploy yet**: public changes and the
application migration remain deferred. **Stop before Phase 5**; Efficient AI
experiments require separate authorization.
