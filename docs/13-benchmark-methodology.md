# 13 — Phase 5 experiment contract

Authorized on 2026-11-10. Finish at Phase 5; Dockerization is removed from the
current scope. Run all experiments locally, preserve application data and active
Q4 defaults, and retain manual local-running and deployment instructions for an
engineering/recruiter audience. Public deployment remains deferred.

## Measurement rules

Use the existing pinned CPU llama.cpp and exact Llama 3.2 1B Instruct family.
Q8_0 is an additional verified experimental artifact, never a silent active-model
replacement. Preserve Q4 and historical Qwen artifacts/results. Record source,
filename, revision, SHA-256, size, GGUF architecture/tokenizer/template metadata,
and verify a live Q8 answer before comparison. No runtime rebuild is needed.

Native experiments use benchmark-only configuration, no changes to server
production defaults. One warm-up and at least three measured repeats per profile
and prompt. Fixed short/long prompts, temperature0, seed42, outputcap32 initially.
Change one variable per pair: Q4/Q8, threads2/4/6, context1024/2048/4096,
KVf16/q8_0/q4_0 when supported, batch/ubatch, flash attention on/off, and
cache_prompt false/true with a fixed repeated prefix. Record unsupported actual
configurations honestly rather than substituting them. A small fixed objective
quality set records prompts, expected answers, actual outputs and exact checks;
it is not an industry quality benchmark or broad model-intelligence claim.

Every report records the actual command/configuration, runtime/model/source pins,
hardware/load, timestamps, warm-up phase, per-request payload/output, native
usage/timings (including cache_n/prompt_n when provided), TTFT, end-to-end latency,
CPU process deltas, RSS/high-water memory, and server exit status. Persist partial
results on failures. Avoid overwriting results. Generate tables and standalone
charts from raw machine-readable measurements, excluding warm-ups.

## Cache and concurrency

Measure actual runtime cache allocation separately from process RSS. GQA logical
KV bytes use layers * context * KVheads * head_dimension * 2(K,V) * elementbytes;
quantized block overhead/alignment and runtime allocation require real evidence.
Repeated-prefix cache experiments include first uncached warm-up and cached
measured calls; do not describe warm reuse as cold prefill. Verify native cache
counters instead of inferring a hit solely from latency. Note cross-session
privacy concerns and leave application cache_prompt=false.

Native slot-concurrency experiments compare 1/2/4 clients, three waves each,
throughput, mean/median/p95 request latency, TTFT, CPU/memory and errors. Compare
serial native slots versus native continuous batching (e.g. four slots), with
constant per-slot context. This is an experiment, not a change to application
admission defaults.

Full stack experiments use temporary loopback certificate-verified HTTPS Caddy,
backend, isolated migrated *_test schema, and owned native server. Compare FIFO
one-active/two-waiters against immediate-reject one-active/zero-waiters with
1/2/4 simultaneous clients. Three measured waves after a separate warm-up,
unique conversations and generous test-only rate/quota limits. Save errors and
rejections as denominators, wall-clock throughput, latency/TTFT, queue wait
(measured server-side), process resource snapshots. Never benchmark against
application data. No CA installation, ACME, firewall or service changes.

Separate model prompt/decode timing only when supplied by native timing fields.
Any client-time-minus-runtime residual combines transport/framework/scheduling
and persistence overhead; it is not precise pure network time. Queue wait must
be a measured interval, not inferred from a latency difference. p95 on small
samples is descriptive, not a capacity guarantee. Report rejection rates over
all measured attempts, not successes only.

## Independent verification

Benchmark configuration validation, event/usage/counter collection, warm-up
exclusion, percentiles, failure recording, raw-result consistency, reports, and
cleanup need independent tests. Existing whole-stack opt-in HTTPS test must pass
along with ordinary and database/inference suites. Keep tests separate from the
actual performance measurements. Real experiments run serially to avoid mutual
CPU contention; tests must not run native servers during measured sweeps.

## Reproduce locally

Use the activated `llama` Conda environment, the existing pinned runtime, and
personally accepted model access. Additional Q8 bytes are experimental; the
application continues to select Q4. Choose fresh output names on each run.
The sweep can take several minutes on this CPU; let it finish before starting
another model process or the whole-stack suite.

```bash
python -m benchmarks.scripts.experiment_artifacts q4
python -m benchmarks.scripts.experiment_artifacts q8 --download
python -m benchmarks.scripts.experiments \
  --config benchmarks/configs/phase5-native.json \
  --output benchmarks/results/local/native-new.json
python -m benchmarks.scripts.experiments --probe-cache \
  --output benchmarks/results/local/allocation-new.json
python -m benchmarks.scripts.experiments \
  --config benchmarks/configs/phase5-prefix-confirmation.json \
  --output benchmarks/results/local/prefix-new.json
python -m benchmarks.scripts.report_experiments \
  benchmarks/results/local/native-new.json \
  --output-dir benchmarks/results/local/native-report-new
python -m benchmarks.scripts.report_experiments \
  benchmarks/results/local/prefix-new.json \
  --output-dir benchmarks/results/local/prefix-report-new
```

Cache probes use verbosity4 only for allocation evidence, without timed request
comparisons. The sweep uses ordinary verbosity and reports process RSS separately.
The hardened runner forces `cache_prompt=false` on each prompt's first warm-up,
then enables reuse only for the configured cached profile. The initial saved
sweep predates that correction for its long cache warm-up; the separate prefix
record verifies zero-cache warm-ups and positive measured cache counters.

For end-to-end measurements, provide a PostgreSQL test URL and a verified Caddy
binary (installed or staged only for testing). Caddy tests recognize
`LLAMA_TEST_CADDY` or the ignored staged binary used in Phase4. OpenSSL creates a
temporary certificate; the HTTPX client explicitly verifies it. All listeners
bind127.0.0.1 and all writes use a new isolated schema in the dedicated `_test`
database. No application migrations or data changes are required.

```bash
source .runtime/database.env
unset LLAMA_DATABASE_URL
python -m benchmarks.scripts.stack_experiment \
  --config benchmarks/configs/phase5-stack.json \
  --output benchmarks/results/local/stack-new.json
```

The stack JSON includes its summary; curated tables and standalone SVGs in the
saved run derive from those fields. No production rate/quota/admission default
is changed: the experimental backend receives generous test-only quotas and
queue settings. The fixed synthetic HTTPS requests stand in for a browser's API
requests; DOM painting and internet paths are not timed.

After all performance runs have stopped, verify the whole application:

```bash
ruff check .
ruff format --check .
pytest
pytest --run-database --run-inference
```

The ordinary suite does not load model weights or use PostgreSQL. When Caddy is
available it can start temporary loopback proxy tests. Database/native work stays
explicitly opt-in and missing dependencies fail an explicitly requested live run.
The current saved run is [2026-11-10-phase5](../benchmarks/results/2026-11-10-phase5/README.md).
