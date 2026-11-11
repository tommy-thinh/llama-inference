# 16 — Performance findings and engineering decisions

These are real **2026-11-10 local measurements** on the six-vCPU Xeon Platinum
8272CL VPS, with 16GB RAM, no GPU, pinned llama.cpp and Meta Llama3.2 1B Instruct.
They are x86 CPU observations: no Qualcomm processor, NPU, mobile power draw, or
GPU was tested. The engineering contribution is reproducible investigation of
latency, memory, correctness, caching, and overload rather than a hardware claim.

The [curated experiment record](../benchmarks/results/2026-11-10-phase5/README.md)
links raw data, exact configurations, frozen source, tables and standalone charts.
The native sweep contains18 profiles and232 successful requests, including58
warm-ups and174 measured requests, plus16 separate quality outputs. The corrected
prefix pair adds16 requests plus8 quality outputs; it is not additional unique
quality coverage. Six separate startup probes record actual native KV allocation.
The HTTPS stack run contains56 attempts across24 waves:14 warm-up attempts and42
measured attempts. It records27 measured successes,15 expected admission rejections,
and zero other measured errors. All performance tests ran before the final test
suite, with no overlapping inference experiments.

## Weight quantization: smaller was faster here, not universally

Four threads,2048 tokens per slot, f16 KV, one client, temperature0, seed42 and
32 output tokens; each row has three measured repeats after a warm-up:

| Weights | GGUF MiB | Peak RSS MiB, long | Short TTFT s | Short decode tok/s | Long TTFT s | Long median latency s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Q4_K_M |770.28|1366.5|0.421|21.19|2.279|3.935|
| Q8_0 |1259.88|1408.1|0.802|11.30|4.004|6.801|

TTFT/latency are medians; decode is the arithmetic mean of native decode rates.
Q8's file is63.6% larger, but its observed peak RSS is only about3.0% larger.
These are different measures. For example, the separate Q4 startup trace reports
a445.50MiB `CPU_REPACK` model buffer, beyond mapped GGUF storage, plus KV and
compute buffers. That illustrates runtime allocation overhead; it does not fully
attribute the entire Q4/Q8 RSS difference. RSS/high-water includes mapped/shared
pages and process allocations, not just model tensor bytes or exclusive RAM.

On the fixed eight-case objective set, Q4 passes**4/8** and Q8**5/8**. Both answer
addition, multiplication, extraction and simple logic correctly. Q8 additionally
answers the KiB question correctly. Both fail other cases, and sequence answers
hit the32-token cap while ignoring the requested concise format. This small
exact-match task checks correctness and instruction-following together. It is not
a statistically supported quality ranking, perplexity evaluation, or evidence
that either model is reliable for general advice. Raw prompts, patterns, outputs,
finish reasons and scores are retained. Keep the existing UI accuracy warning.

**Decision:** retain Q4_K_M as the active artifact. Q8's substantial speed cost
and small, narrow score difference do not justify changing this constrained demo.

## CPU settings and context: measure variability as well as averages

| Profile | Short median TTFT s | Short mean decode tok/s | Long median latency s | Long peak RSS MiB |
| --- | ---: | ---: | ---: | ---: |
|2 threads|0.808|13.06|7.328|1365.9|
|4 threads, initial control|0.421|21.19|3.935|1366.5|
|6 threads|0.316|25.09|3.284|1368.5|
|4 threads, final control|0.406|24.06|3.579|1366.5|
|1024 context,4 threads|0.449|22.31|3.691|1334.5|
|4096 context,4 threads|0.415|22.38|3.872|1430.5|

Six threads were fastest on the short prompt in this pass, but one long-prompt
sample fell to6.36 decode tokens/s and6.985 seconds total. The four-thread short
control changed from21.19 to24.06 tokens/s across the sweep, about13.5%. Cloud
contention, scheduling, warming and fixed run order can therefore confound small
improvements. Three repeats do not separate all those causes. Do not discard the
outlier or advertise six threads as a universal optimum.

Context capacity is not prompt length: these rows use the same51/310-token prompts
and32-token output budget. Increasing capacity1024→4096 raised long peak RSS by
96MiB, matching the separately measured f16 KV increase32→128MiB. It did not make
these fixed prompts three times longer. Longer actual prompts are a separate
prefill cost, illustrated by the short/long columns.

Batch64/ubatch32, batch256/ubatch128, batch512/ubatch256 and Flash Attention off/on
all ran successfully. Full tables include them; several differences are comparable
to control drift. Native supports CPU Flash Attention, and quantized V requires it.
No runtime rebuild, custom cache, BLAS switch or GPU optimization was introduced.

**Decision:** keep four threads and4096 default context as the tested application
settings. Deployment's prepared300% CPU ceiling was **not active** during these
experiments; repeat measurements under that ceiling before promising public
performance. The native four-thread runs consumed approximately four cores' time.

## KV precision and prefix reuse

At2048 tokens in one slot, the runtime actually reports64MiB f16,34MiB q8_0 and
18MiB q4_0 KV buffers. Four slots at2048 each require256MiB f16. These are measured
allocation lines, separate from logical estimates and process RSS. The initial
performance sweep's default verbosity omitted allocation lines; six dedicated
verbosity4 startup probes provide this evidence without changing timed runs.

Quantized caches reduced measured process RSS modestly, while long-prompt TTFT
was2.629/2.651 seconds for q8_0/q4_0 versus2.279 f16 in the initial control.
Decode rates varied, and no broad cache-precision quality test was performed.
Memory savings alone do not establish a net application improvement.

The fresh controlled prefix pair measured long median TTFT**2.273→0.052 seconds**.
Native counters confirm309 reused tokens and one newly evaluated token, versus
zero reused tokens in the control. Both prompt warm-ups have `cache_n=0`.
The initial sweep's long prefix warm-up reused30 shared chat-header tokens; that
original evidence is preserved and disclosed, while the corrected pair is the
published cache comparison. Warm-cache TTFT is not cold prefill performance.

**Decision:** retain f16 KV and `cache_prompt=false` in the application. Prefix
reuse is powerful for repeated prompts, but cross-session cache isolation,
real traffic hit rates, and output-quality effects require more work before
turning it on for anonymous users. See [cache/concurrency analysis](15-cache-and-concurrency.md).

![Measured prefix TTFT](../benchmarks/results/2026-11-10-phase5/prefix-report/median_ttft_seconds.svg)

![Native KV allocation](../benchmarks/results/2026-11-10-phase5/kv-allocation.svg)

## Throughput is different from per-user latency

At four clients on the short native workload, serial one-slot throughput was
17.82 output tokens/s with p95 latency7.284s. Four slots with continuous batching
reached31.31 tokens/s with p95 latency4.182s, but peak RSS rose from1355.8 to1568.4MiB
and per-slot context stayed2048 (total capacity increased fourfold). Disabling
continuous batching reduced throughput to27.64 tokens/s in that four-slot case.
These are synthetic unthrottled native experiments, not a new application setting.

The certificate-verified loopback HTTPS path exercised Caddy, FastAPI, PostgreSQL
and native inference. With four clients, the existing one-active/two-waiter FIFO
admission completed9/12 requests, rejected3/12, sustained18.19 tokens/s, and had
successful-request p95 latency5.474s. Immediate rejection completed3/12 and
rejected9/12, with p95 successful latency2.067s. Faster rejected requests are not
faster answers. Both strategies kept native work bounded; FIFO traded waiting
for more completed requests. Expected503 responses remain in the denominator.

For FIFO with one client, median observed latency was1.700s. Its three measured
native prompt times were344–462ms and decode times1272–1310ms. Subtracting queue
wait and those native durations leaves about44–47ms, combining TLS/HTTP,
framework, scheduling, persistence, and streaming. This is **not precise network
latency**, and browser painting/DOM rendering was not timed. Queue wait is an
actual backend monotonic interval exposed in SSE metadata, not a guessed residual.

**Decision:** retain one active application generation and two waiters. Native
batching is a measured candidate for a future workload, but changing application
concurrency would require revalidating quotas, fairness, memory, cancellation,
and deployment ceilings. No additional implementation phase is authorized.

## Limits and reproducibility

- One small fixed workload set, one VPS, fixed profile order, and three repeats;
  p95 with3/6/12 observations is descriptive and often the maximum observation.
- Page cache and mapped model memory were warm after startup/warm-up. These are
  not OS-cache-cold disk-load tests or hardware energy measurements.
- CPU is process user+system time, with100% equal to one logical CPU. Native
  concurrent CPU attribution is per wave, not per request. Stack CPU percentages
  are approximate: resource counters are read after collection/progress writes,
  slightly later than the worker-completion interval used for throughput. Raw
  sampling times are retained; exact counter-window extension is unknown.
- Process high-water RSS is cumulative within a profile. It is not exact
  per-request transient memory or whole-machine exclusive physical memory.
- The original native sweep ran from a frozen source snapshot. Later fixes harden
  schema validation, failure evidence, CPU bookkeeping, and cache warm-up controls;
  they do not alter its completed measurements. The corrected prefix run uses
  the updated code. Source hashes, frozen files and the correction record are
  retained; no original result was overwritten or recast as a later run.
- HTTPS is private loopback with a temporary client-trusted certificate. No ACME,
  internet latency, installed systemd ceiling, public exposure, Qualcomm hardware,
  or graphical browser automation was tested. The whole-stack automated suite
  separately verifies restart, persistence, cancellation, ownership, and failure.

Use [the methodology](13-benchmark-methodology.md) for exact reproduction commands,
[local running](../README.md#run-the-local-web-application) to inspect the app, and
[manual deployment](09-deployment-guide.md) for a future operator walkthrough.
The project finishes at Phase5; Dockerization is removed. Documentation can later
be polished for an engineering/recruiter audience while preserving these facts.
