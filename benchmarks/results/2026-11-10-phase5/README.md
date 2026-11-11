# CPU inference benchmarks — 2026-11-10

Meta Llama 3.2 1B Instruct on a six-vCPU Intel Xeon VPS, using pinned CPU llama.cpp.
The active application uses Q4_K_M; these records also contain experimental settings.

| Record | Contents |
| --- | --- |
| `native.json` | 18 profiles;232 successful requests (58 warm-up,174 measured);16 quality outputs |
| `native-report/` | Generated native summaries and standalone charts |
| `prefix.json` and `prefix-report/` | Controlled uncached/reused-prefix pair;16 requests and8 repeated quality outputs |
| `allocation.json`, `allocation-startup.txt`, `kv-allocation.svg` | Six separate verbosity4 KV-allocation probes and chart |
| `stack.json` and `stack-report/` | Certificate-verified loopback HTTPS stack;42 measured attempts,27 successful responses,15 expected admission rejections |
| `q8-source-metadata.json`, `q8-verification.json`, `../../artifacts.lock.json` | Pinned conversion provenance and integrity checks |
| `source/*.py.txt` | Frozen measurement-time source stored as text evidence |
| `verification.json` | Measurement counts, source hashes, corrections and limitations |

Native runs use temperature0,seed42,32 output tokens and fixed51/310-token prompts.
Warm-ups are excluded from summaries. Native concurrency holds2048 context tokens
per slot; four slots allocate8192 total. Process CPU100% means one logical CPU.
The quality set is an eight-case exact-match check, not a broad model evaluation.

For prefix reuse, use the separate controlled pair: both first warm-ups have zero
cached tokens. Its measured calls reuse50/309 tokens. The initial sweep's long
warm-up reused30 shared-header tokens; that unmodified record is retained for
traceability. Timed logs and process RSS are distinct from the separate allocation
probes. Stack CPU counter windows are approximate; throughput uses worker completion
timestamps. Original provenance may name paths outside the curated tree.
`curation-manifest.json` records current retained-file hashes and explicitly
identifies changed or archived entries in the measurement-time verification tree.

The repeated four-thread control differed by13.5% in short decode speed, and one
six-thread long request was an outlier. Interpret small changes accordingly.
See [methodology, findings and reproduction](../../../docs/benchmarks.md).
