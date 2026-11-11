# Resource-constrained Llama inference

A learning and engineering project for serving **Meta Llama 3.2 1B Instruct**
with llama.cpp on a CPU-only VPS. Phase 1 establishes verified native inference
and a measured baseline. Phase 2 adds a local FastAPI backend and vanilla
JavaScript chat page with progressive streaming and cancellation. Phase 3 adds
PostgreSQL sessions, saved conversations, and request metrics. Phase 4 security
controls and deployment templates are prepared locally. Phase5 adds measured
quantization, KV-cache, CPU, prefix-reuse and concurrency experiments. **Deployment
is deferred; Dockerization is outside the final scope.**

## Model access status

The model page is manually gated and the model uses Meta's Llama 3.2 Community
License. Tommy confirmed acceptance on 2026-11-08. The pinned Llama weights are
downloaded in `.runtime/models/` and pass checksum/GGUF verification. Real
generation, streaming, and template/tokenization tests pass. The full
Llama baseline is recorded in [the benchmark guide](docs/03-baseline-benchmark.md). Existing Qwen results are historical.

Before downloading, review and accept the terms on the
[official model page](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct)
and read the [Community License](https://dev.meta.ai/llama/llama3_2/license).
Then, from the repository root:

```bash
python -m inference.setup --jobs 3 --accept-llama-license
python -m inference.setup --verify-only
```

The flag confirms that you personally accepted the terms; it does not accept
them for you. The selected artifact, converter revision, filename, expected
size, SHA-256, and metadata checks are pinned in
[`inference/artifacts.lock.json`](inference/artifacts.lock.json).

## Python environment

Miniconda manages the project Python environment. Create and activate it from
the repository root:

```bash
conda env create -f environment.yml
conda activate llama
```

The file uses only `conda-forge` (`nodefaults` excludes configured default
channels) and pins Python 3.11 plus the application/test dependencies.
These packages are available from Conda, so no pip-only dependency is needed.
`fastapi-core` provides the Python FastAPI API without unused CLI extras. Node.js
is only a development test runner; the application needs no Node server or npm packages.
The `nodefaults` channel entry is documented in
[Conda's environment-file guide](https://docs.conda.io/projects/conda/en/stable/user-guide/tasks/manage-environments.html).

This VPS's Conda terms plugin checks globally configured default channels before
processing an environment file. Since those terms have not been accepted, the
tested install used explicit per-command channel overrides instead:

```bash
conda create --override-channels -c conda-forge -n llama python=3.11 -y
conda install --override-channels -c conda-forge -n llama \
  httpx=0.28.1 pytest=9.1.1 ruff=0.17.1 anyio=4.16.0 \
  fastapi-core=0.162.2 uvicorn=0.54.0 pydantic=2.14.3 nodejs=24.22.1 \
  sqlalchemy=2.0.54 alembic=1.20.1 psycopg=3.3.8 -y
conda activate llama
```

This does not change global Conda settings or accept terms for other channels.

## Run the local web application

Activate `llama` in each terminal. In terminal 1:

```bash
python -m inference.server
```

Keep it running. PostgreSQL is installed and running on this VPS. In terminal 2,
load the private local database credentials and apply the schema:

```bash
source .runtime/database.env
alembic upgrade head
python -m backend
```

Open **http://127.0.0.1:8000** in your browser. Send a prompt to watch the answer
stream; Stop Generation cancels the current request and saves partial text.
Refresh restores saved history. Use New Conversation or the conversation selector
to switch chats. The anonymous browser cookie controls access to its history.
Output defaults to 1,024 tokens, with a maximum of 2,048. The native context
defaults to 4,096 tokens to leave room for both prompt and generated output.
The backend connects to the existing model process at `127.0.0.1:8081`.
Press Ctrl+C in each terminal to stop its foreground service. PostgreSQL remains
a background OS service. Use one backend worker; startup recovers interrupted
requests. For a new machine, follow the [database setup guide](docs/07-database-design.md#local-setup).

For a VPS, run this on your own computer, using your SSH login:

```bash
ssh -N -L 8000:127.0.0.1:8000 USER@VPS_HOST
```

Then open `http://127.0.0.1:8000` on your computer. Both services remain bound to
loopback. The [running guide](docs/02-running-llama.md) has direct inference API
examples; [backend architecture](docs/04-fastapi-architecture.md) explains the web API.

Weights, builds, and logs stay under ignored `.runtime/`.

## Verify and benchmark

```bash
ruff check .
ruff format --check .
pytest
pytest --run-inference
# Dedicated test database, independent from application data:
source .runtime/database.env
unset LLAMA_DATABASE_URL
pytest --run-database
pytest --run-database --run-inference
python -m benchmarks.scripts.run --config benchmarks/configs/smoke.json
python -m benchmarks.scripts.run --config benchmarks/configs/baseline.json
```

Ordinary tests use neither PostgreSQL nor a loaded model. Database tests require
`LLAMA_TEST_DATABASE_URL`, refuse names without the `_test` suffix, and migrate
then drop only their own temporary schemas. `--run-inference` starts and stops temporary
localhost servers; missing assets fail the explicitly requested live suite.
Benchmarks own their server processes and write new results under ignored
`benchmarks/results/local/`.

## Read and learn

- [Current phase, blocker, and verification](docs/project-status.md)
- [Inference fundamentals and GQA/KV-cache intuition](docs/01-inference-fundamentals.md)
- [Setup, model terms, and local API](docs/02-running-llama.md)
- [Llama baseline status and reproduction plan](docs/03-baseline-benchmark.md)
- [Historical Qwen baseline, preserved separately](docs/03-qwen-historical-benchmark.md)
- [Runtime and model-migration decisions](docs/decisions/0002-llama-model-migration.md)
- [FastAPI architecture and API contract](docs/04-fastapi-architecture.md)
- [HTTP streaming, deadlines, and cancellation](docs/05-http-streaming.md)
- [Frontend behavior and local web usage](docs/06-frontend-backend-communication.md)
- [Database design, ER diagram, setup, and retention](docs/07-database-design.md)
- [Anonymous sessions, saved history, and API examples](docs/08-sessions-and-persistence.md)
- [Efficient inference fundamentals](docs/12-efficient-ai-fundamentals.md)
- [Benchmark methodology and reproduction](docs/13-benchmark-methodology.md)
- [Q4/Q8 artifacts and quality checks](docs/14-quantization-experiments.md)
- [Cache and concurrency measurements](docs/15-cache-and-concurrency.md)
- [Performance findings and decisions](docs/16-performance-findings.md)
- [Scope and revised roadmap](PROJECT_SPEC.md)

Phase 1 is committed as `de5b6b5` (`Phase 1`), Phase 2 as `e9afd64` (`Phase 2`).
Phase 3 is committed as `bbae986` (`Phase 3`). Phase4 is committed as `8e0425b`. Phase5 is the final authorized implementation
milestone. Dockerization is removed from scope. Local testing and future manual
deployment instructions remain required; **do not deploy yet**.


## Prepare for deployment (do not run go-live steps yet)

Read [the manual deployment guide](docs/09-deployment-guide.md) first. It covers
a separate nonroot application copy, private environment, database migration and
runtime roles, Caddy, HTTPS, systemd limits, validation, and the go-live checklist.
You will need a domain, ACME email, and monitored abuse contact before launch.

The public profile is opt-in with `LLAMA_PUBLIC_ORIGIN=https://YOUR_DOMAIN`, a
database URL, and Secure cookies. It disables transient chat, enforces same-origin
writes, limits API bodies/rates, reserves durable daily quotas, and permits one
generation plus two waiters. See [security and resource controls](docs/10-security-and-resource-management.md).

Migration `0002` and `deploy/` files are prepared, **not applied to this host**.
The existing application database is still at its Phase 3 schema. Deploying later
requires the explicit reviewed migration; `/ready` checks quota-table availability.
No domain, firewall, public service, or public certificate was configured.
[Operations and troubleshooting](docs/11-operations-and-troubleshooting.md) covers
restart, backup, isolated restore, update, and rollback. Public HTTPS and installed
service recovery remain future deployment checks.

The ordinary suite can also start a temporary loopback Caddy proxy when a staged
or installed test binary is available. Use `LLAMA_TEST_CADDY=/path/to/caddy` to
select one; otherwise its configuration/proxy cases report skips. Full integration:

```bash
source .runtime/database.env
unset LLAMA_DATABASE_URL
pytest --run-database --run-inference
```

Tests use temporary listeners and isolated `_test` schemas; they do not deploy.


## Measured Phase5 findings

The [curated run](benchmarks/results/2026-11-10-phase5/README.md) contains18 native
profiles, verified Q4/Q8 artifacts, actual KV allocation probes, a corrected
prefix-cache comparison, and a certificate-verified loopback HTTPS stack benchmark.
Raw records, exact configurations, outputs, tables, charts and source hashes are
retained. The [findings](docs/16-performance-findings.md) explain uncertainty and
why active serving defaults remain conservative.

- Q4 vsQ8 short decode:21.19 vs11.30 tokens/s; eight-case toy quality:4/8 vs5/8.
- At2048 context, actual f16/q8_0/q4_0 KV allocation:64/34/18MiB.
- Repeated long-prefix TTFT:2.273→0.052s, confirmed by309 cached native tokens.
- Four native clients: continuous batching31.31 vs serial17.82 output tokens/s,
  with higher memory allocation. Application admission remains one active/two waiters.

These are small synthetic x86 VPS experiments, not Qualcomm/NPU measurements or
public-service guarantees. Control drift and a six-thread outlier limit conclusions.
Future documentation polish should present both the local-running and manual
deployment paths to engineering reviewers/recruiters without implying a public
service or container deployment was tested.
