# 03 — Llama CPU baseline

## Current status

The complete baseline ran on **2026-11-08**, from `2026-11-07T20:42:03.006904+00:00` to
`2026-11-07T20:47:37.000932+00:00`. All **56 streaming requests** succeeded: 14 warm-ups and
42 measured requests across seven serial profiles. All seven owned servers
exited with code 0; every request reports `cache_n=0`.

Tommy confirmed personal license/access acceptance before download. The local
Q4_K_M artifact matches its exact pinned 807,694,464-byte size, SHA-256, tokenizer,
architecture/GQA dimensions, and chat template. The full live suite verifies real
generation and streaming; this experiment measures serial performance.

- [Raw measured requests and machine/configuration snapshot](../benchmarks/results/2026-11-08-llama-baseline/raw.json)
- [Complete measured summary](../benchmarks/results/2026-11-08-llama-baseline/summary.md)
- [Decode-speed chart](../benchmarks/results/2026-11-08-llama-baseline/decode-speed.svg)
- [Exact benchmark-time source snapshot](../benchmarks/results/2026-11-08-llama-baseline/source.tar.gz)
- [Final live-suite JUnit](../benchmarks/results/2026-11-08-llama-baseline/tests.xml)
- [Verification and source-delta record](../benchmarks/results/2026-11-08-llama-baseline/verification.json)

| Threads | Short/long TTFT median (s) | Short/long decode mean (tok/s) | Peak RSS (MiB) |
| --- | --- | --- | ---: |
| 2 | 0.947 / 4.572 | 11.44 / 10.23 | 1364.7 |
| 4 | 0.491 / 2.412 | 19.76 / 17.83 | 1366.7 |
| 6 | 0.421 / 1.775 | 17.48 / 16.36 | 1368.6 |

These rows use context 2,048 and output cap 64. Four threads have better reported
decode speed than six in this run; six have lower TTFT and request latency but
consume about 560% of one CPU versus about 400% for four threads. This small,
fixed-order VPS experiment does not establish a universal optimum. The default
remains four threads.

At four threads, context capacity 1,024/2,048/4,096 produces peak RSS of
1,334.7/1,366.7/1,430.7 MiB. The actual prompts are fixed; this compares reserved
capacity, not full-window workload lengths. Increasing the output cap from 32 to
128 increases short-prompt median latency from 2.086 to 7.208 seconds and
long-prompt latency from 4.173 to 9.769 seconds.

All eleven archived source files match the source hashes captured at benchmark
start. A later foreground-only launcher fix verifies model/runtime identity and
preserves CLI overrides before exec. It does not change the ManagedServer path
used by this benchmark. The verification record identifies that source delta.

The earlier Qwen result remains available as a separate, explicitly historical
[Qwen baseline](03-qwen-historical-benchmark.md). Its raw measurements were not
copied into the Llama result set.

## Selected artifact and measured experiment

The pinned selection is `meta-llama/Llama-3.2-1B-Instruct`, converted by
`bartowski` to `Llama-3.2-1B-Instruct-Q4_K_M.gguf`. The expected file size is
807,694,464 bytes and the expected SHA-256 is recorded in
[`inference/artifacts.lock.json`](../inference/artifacts.lock.json). Those are
integrity expectations, now matched by the local setup verification on 2026-11-08.

The committed baseline configuration changes one factor at a time:

| Profile | Threads | Context capacity | Output cap |
| --- | ---: | ---: | ---: |
| baseline | 4 | 2,048 | 64 |
| threads-2 | 2 | 2,048 | 64 |
| threads-6 | 6 | 2,048 | 64 |
| context-1024 | 4 | 1,024 | 64 |
| context-4096 | 4 | 4,096 | 64 |
| output-32 | 4 | 2,048 | 32 |
| output-128 | 4 | 2,048 | 128 |

Each of two fixed prompts received one warm-up and three measured requests
per profile: 56 total requests. Requests are serial, use one
server slot, greedy sampling, explicit output caps, SSE usage, and
`cache_prompt=false` to keep cross-request prefix reuse out of the baseline.
The K/V cache remains enabled within each request and uses f16.

## Measurements recorded

The raw JSON is designed to record:

- Exact GGUF size, checksum, quantization metadata, runtime/model pins, and
  source/config hashes.
- Startup time, server process exit status, and current/peak RSS.
- CPU utilization during each request from the server process's user+system CPU
  time counters. The percentage is normalized to **one logical CPU**; values
  above 100% mean several CPU threads were active at once.
- Client TTFT to the first non-empty text delta, total request latency, token
  counts, finish reason, and llama.cpp's reported decode tokens/second.
- Full prompt text, generation settings, machine/software snapshot, and the
  Conda environment name/Python prefix and warm-up/measured label for every
  request. The source hashes include `environment.yml`.

The report table summarizes median TTFT/total latency and mean runtime decode
speed, CPU utilization, and peak RSS by prompt/profile. Runtime decode speed is
not estimated from SSE chunk counts. Startup is reported separately from request
latency. Process CPU percent is not whole-machine CPU load and can be noisy for
short generations.

## Reproduction after accepting access

First follow the manual acceptance steps in [the running guide](02-running-llama.md#license-and-access-checkpoint),
then activate the `llama` Conda environment, set up, and verify the model. From
the repository root:

```bash
python -m inference.setup --jobs 3 --accept-llama-license
python -m inference.setup --verify-only
pytest --run-inference
python -m benchmarks.scripts.run \
  --config benchmarks/configs/baseline.json \
  --output benchmarks/results/local/llama-baseline.json
python -m benchmarks.scripts.report \
  benchmarks/results/local/llama-baseline.json \
  --output-dir benchmarks/results/local/llama-baseline-report
```

The runner refuses to overwrite an existing raw file. Keep all Llama results in
their own `llama-*` paths and leave the Qwen historical files untouched. Run the
smoke configuration first if only pipeline validation is needed; it is not a
performance comparison.

## Measurement limits and Phase 5

The seven-profile sweep varies threads, reserved context capacity, and output
limit but does not measure concurrent throughput, full-window prefill, answer
quality, or another quantization. A larger context setting is a larger reserved
capacity, not a claim that the fixed prompt itself became longer. Shared VPS
contention, CPU frequency and page-cache residency can vary between runs.

Q4 versus Q8, KV-cache quantization, prefix reuse, context scaling, batching,
concurrency, and output-quality trade-offs remain planned Phase 5 work. The
installed llama.cpp documentation lists several of these controls; we will test
their behavior on the selected model before treating an option as useful.

For the original 2026-10-21 numbers and verification records, see the
[historical Qwen benchmark](03-qwen-historical-benchmark.md).
