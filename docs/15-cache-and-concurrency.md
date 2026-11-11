# 15 — Cache and concurrency experiments

A cache saves work by keeping information the model will need again. Concurrency
changes how many requests compete for that work. Both can improve throughput,
but they also consume memory or make some requests wait. This page connects the
controlled experiments to those trade-offs; see [the experiment contract](13-benchmark-methodology.md)
for the full measurement rules and [the fundamentals](12-efficient-ai-fundamentals.md)
for the model and attention background.

## KV allocation and prefix reuse

Each attention layer stores keys and values for prior tokens so decoding need
not recompute them. The pinned model has 16 layers, 8 KV heads and 64 values per
head. For one 2,048-token sequence, f16 K and V require a logical
`16 × 2048 × 8 × 64 × 2 × 2 = 64 MiB`. This is a calculated tensor size,
not process RSS: runtime padding, other allocations, model weights and code are
separate. The experiment records the runtime's KV allocation log and process
RSS independently, including quantized cache configurations and their block
metadata. It keeps context per slot fixed when comparing native slot counts.

Prefix reuse is a different question: a second request with the same beginning
may reuse the previous prompt's state. We compare the same fixed prompt and
settings with `cache_prompt` disabled and enabled. The first call warms the
cache; measured calls are warm reuse, not cold prefill. Native `cache_n` and
`prompt_n` counters are the evidence for reused versus newly processed tokens;
TTFT alone cannot demonstrate a cache hit. Application requests continue to use
`cache_prompt=false` because sharing state across unrelated sessions raises
isolation and privacy concerns.

## Native and full-stack concurrency

Native 1, 2 and 4-client waves compare a single serial slot with four slots and
continuous batching. Multiple slots may allow more simultaneous work, but can
also increase memory, contention, and the time each request waits. Context is
kept constant per slot, so the total allocated context rises with slot count.
These are experiment-only settings, not a change to application defaults.

The full-stack run goes through a temporary loopback Caddy HTTPS proxy, one
backend worker, a freshly migrated isolated schema in a dedicated `_test`
database, and the pinned Q4 CPU server. The client verifies the temporary
certificate. The proxy and server are owned by the experiment and stopped on
exit; no system CA, services, or public listener are changed. It compares one
active request with two FIFO waiters against one active request with zero
waiters (immediate rejection), for 1, 2 and 4 simultaneous clients. Each cell
has one separate warm-up and three measured waves, with a fresh conversation
for each attempt and a 32-token output cap. Test-only quotas are generous.

A rejection is an attempted request, so rejection rate uses all measured
attempts as its denominator; throughput counts successful completions over
measured wave wall time. Report both rather than interpreting a fast rejection
as a fast answer. Queue wait is measured by the backend at admission, while
TTFT and total latency are observed by the client. Any client-time-minus-native
runtime residual combines framework, scheduling, transport, persistence and
streaming costs; it is not an exact network measurement. With three waves per
cell, nearest-rank p95 describes this small sample rather than a capacity
promise. The traffic is loopback and synthetic, so it cannot predict internet
latency or real-user behavior.

## Reproduction

Activate the existing `llama` environment and stop other model experiments before
running. The model and runtime must already be verified, and the test URL must
refer to a dedicated loopback PostgreSQL database ending `_test`; never point the
experiment at application data. Load the ignored credentials privately, then
unset the application URL. Choose a new output path because the runner refuses
to overwrite results.

```bash
set -a
source .runtime/database.env
set +a
unset LLAMA_DATABASE_URL
python -m benchmarks.scripts.stack_experiment \
  --config benchmarks/configs/phase5-stack.json \
  --output benchmarks/results/local/phase5-stack-new.json
```

The raw JSON contains every wave, attempt, stream frame, error, server-side queue
wait, resource snapshot and process exit status. Warm-ups are excluded from
summaries. Keep raw results and their configuration together when comparing runs.

## Results on this machine, 2026-11-10

The separate startup allocation probes logged 32, 64 and 128 MiB for one f16
slot at 1,024, 2,048 and 4,096 context tokens, respectively. At 2,048 tokens,
the runtime logged 34 MiB with q8_0 K/V and 18 MiB with q4_0 K/V. Four f16
slots at 2,048 tokens per slot allocated 256 MiB. These particular allocations
match the logical/block estimates, but they are runtime log measurements, not
RSS or a general guarantee for other models and builds. See the
[allocation raw record](../benchmarks/results/2026-11-10-phase5/allocation.json).

The separate prefix control confirmed zero reused tokens in both first warm-ups
and all uncached control requests. With reuse enabled, measured calls reused
50 tokens for the short prompt and 309 for the long prompt. The median client
TTFT changed from 0.398 to 0.053 seconds for the short prompt and from 2.273 to
0.052 seconds for the long prompt. These are warm repeated prompts on this
machine, with only three measured requests per cell; they are not cold-request
speedups. See [prefix raw measurements](../benchmarks/results/2026-11-10-phase5/prefix.json).

The following native results compare serial slots with four continuous-batching
slots using the same per-slot 2,048-token context. Throughput is completion
tokens divided by measured wave wall time. The one-client baseline shares the
serial configuration. All shown attempts succeeded.

| Prompt | Clients | Slots | Success/attempts | Median TTFT s | Median latency s | p95 latency s | Completion tokens/s | Peak RSS MiB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Short | 1 | 1 | 3/3 | 0.421 | 1.892 | 1.949 | 16.986 | 1355.492 |
| Short | 2 | 1 | 6/6 | 1.260 | 2.619 | 3.500 | 18.326 | 1355.480 |
| Short | 2 | 4 | 6/6 | 0.778 | 2.798 | 2.883 | 22.785 | 1557.340 |
| Short | 4 | 1 | 12/12 | 3.071 | 4.455 | 7.284 | 17.823 | 1355.828 |
| Short | 4 | 4 | 12/12 | 1.508 | 4.038 | 4.182 | 31.306 | 1568.367 |
| Long | 1 | 1 | 3/3 | 2.279 | 3.935 | 3.967 | 8.200 | 1366.520 |
| Long | 2 | 1 | 6/6 | 4.300 | 5.949 | 8.410 | 8.056 | 1366.520 |
| Long | 2 | 4 | 6/6 | 4.208 | 6.849 | 6.918 | 9.362 | 1565.570 |
| Long | 4 | 1 | 12/12 | 8.030 | 9.731 | 15.680 | 8.236 | 1367.000 |
| Long | 4 | 4 | 12/12 | 6.721 | 11.860 | 12.409 | 10.611 | 1568.664 |

For example, four short-prompt clients achieved higher aggregate throughput
with four slots, at a higher peak RSS. For long prompts, four slots improved
throughput but increased median latency in this sample. This illustrates why a
single throughput number does not describe an individual user's experience.
[Native raw measurements](../benchmarks/results/2026-11-10-phase5/native.json)
include per-request timing and resource evidence.

The HTTPS stack produced these measured results; rates and latency exclude the
warm-ups. Median and p95 attempt latency include rejections, so low values in a
reject-heavy cell must not be read as successful-response latency. Queue wait
and TTFT columns include successful requests only.

| Admission | Clients | Success/attempts | Rejected | Errors | Successes/s | Completion tokens/s | Median attempt s | p95 attempt s | Median TTFT s | Median queue wait s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FIFO | 1 | 3/3 | 0 | 0 | 0.577 | 18.461 | 1.700 | 1.817 | 0.428 | 0.000 |
| FIFO | 2 | 6/6 | 0 | 0 | 0.573 | 18.329 | 2.667 | 3.545 | 1.290 | 0.814 |
| FIFO | 4 | 9/12 | 3 | 0 | 0.568 | 18.191 | 2.625 | 5.474 | 2.171 | 1.702 |
| Reject | 1 | 3/3 | 0 | 0 | 0.581 | 18.593 | 1.718 | 1.728 | 0.438 | 0.000 |
| Reject | 2 | 3/6 | 3 | 0 | 0.556 | 17.804 | 0.920 | 1.837 | 0.437 | 0.000 |
| Reject | 4 | 3/12 | 9 | 0 | 0.537 | 17.190 | 0.120 | 2.067 | 0.473 | 0.000 |

At four clients FIFO admitted nine of twelve measured attempts, while immediate
rejection admitted three; both sustained roughly half a successful request per
second. FIFO's accepted requests paid queue delay. Zero values above are rounded
to three decimals, not evidence that admission took exactly zero time. The
[stack raw record](../benchmarks/results/2026-11-10-phase5/stack.json) contains
all 24 warm-up and measured waves, individual responses and queue metadata. The
isolated schema was removed and owned processes exited after the run.
