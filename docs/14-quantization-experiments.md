# 14 — Quantization experiments

Phase 5 compares two quantizations of the **same Meta Llama 3.2 1B Instruct
model**, using the existing CPU-only llama.cpp build. The active application
continues to use Q4_K_M. Q8_0 is an additional experiment artifact, recorded in
[the experiment manifest](../benchmarks/artifacts.lock.json), without modifying
[the active manifest](../inference/artifacts.lock.json). Historical Qwen data
remains labeled historical evidence and is not a comparison arm.

## Artifact provenance and checks

Tommy confirmed the Meta model terms on 2026-11-08. This experiment adds no new
model family and performs no terms acceptance. Both conversions come from
`bartowski/Llama-3.2-1B-Instruct-GGUF`, revision
`067b946cf014b7c697f3654f621d577a3e3afd1c`. The original upstream is
`meta-llama/Llama-3.2-1B-Instruct`, pinned to
`9213176726f574b556790deb65791e0c5aa438b6`; preserve its existing manual-access
attestation. Source file sizes and SHA-256 digests were fetched on 2026-11-10
from the conversion repository's [pinned Hugging Face API metadata][metadata].
The [pinned conversion card][card] identifies llama.cpp b3821 with an importance
matrix as the conversion recipe; it is distinct from our serving runtime.

| Artifact | Filename | Exact bytes | MiB |
| --- | --- | ---: | ---: |
| Active Q4_K_M | `Llama-3.2-1B-Instruct-Q4_K_M.gguf` | 807,694,464 | 770.28 |
| Experimental Q8_0 | `Llama-3.2-1B-Instruct-Q8_0.gguf` | 1,321,083,008 | 1,259.88 |

```text
Q4_K_M SHA-256
6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83
Q8_0 SHA-256
432f310a77f4650a88d0fd59ecdd7cebed8d684bafea53cbff0473542964f0c3
```

Q8 is about 1.636 times the file size of Q4; that is a storage comparison, not
a measured process-memory or performance ratio. Before the full Q8 download,
a 16 MiB range read supplied its GGUF header. Comparing that header with the
previously verified local Q4 header found the same 147 tensors and identical
metadata except `general.file_type` (`15` for Q4_K_M, `7` for Q8_0). In
particular, architecture, layer/head counts, tokenizer metadata, vocabulary
summary/special-token markers and the complete chat-template string agree.
The template SHA-256 is
`5816fce10444e03c2e9ee1ef8a4a1ea61ae7e69e438613f3b17b69d0426223a4`.

The complete Q8 artifact was downloaded to a temporary sibling file, checked
against its trusted size and SHA-256, then published atomically without
overwriting any existing model. Full-file validation also checks GGUF v3,
architecture, tensor count, selected tokenizer IDs, vocabulary length and
special tokens, and the exact template checksum. Byte identity to the trusted
digest covers all tokenizer arrays beyond the summarized header checks.
The ignored local verification record is `.runtime/q8-artifact-verification.json`.
Live-inference verification must accompany the measured results before treating
the artifact as a successful comparison arm.

## Reproduce artifact verification

With `conda activate llama` already active:

```bash
python -m benchmarks.scripts.experiment_artifacts q4
python -m benchmarks.scripts.experiment_artifacts q8 --download
python -m benchmarks.scripts.experiment_artifacts q8
```

An existing file is verified and reused. A wrong existing file causes failure;
the downloader does not replace or delete it. Temporary partial downloads are
removed on handled failures. Downloading never changes the active model path,
runtime build, source pin or local model attestation.

## Controlled comparisons

Follow [the experiment contract](13-benchmark-methodology.md): same prompt
dataset, model family, runtime revision, CPU flags, thread count, context,
batch sizes and KV types; temperature 0, seed 42 and a 32-token initial cap.
Warm up separately and take at least three measured repeats for each fixed
short/long prompt. Capture native prompt/decode timing, client TTFT and latency,
process CPU deltas, RSS/high-water memory and failures. Model-load time is a
separate startup cost, not request TTFT. Warming each model does not demonstrate
cold operating-system page-cache behavior, and the experiment does not drop
host caches or interrupt existing services.

Use the fixed objective quality cases in
[`benchmarks/configs/quality.json`](../benchmarks/configs/quality.json).
Record the expected answer, full actual output and exact pass/fail check for
every case. These deliberately small arithmetic/extraction checks catch obvious
regressions but do not establish broad intelligence, factuality or real-user
quality equivalence. Report answer checks separately from speed and memory.

Quantization perturbs weights, so deterministic sampling does not guarantee
identical text across Q4 and Q8. A shorter response can make total latency lower
without making decoding faster. Compare native token counts and rates alongside
end-to-end duration, and retain early EOS/short outputs in the raw evidence.

## Runtime support boundaries

The installed llama-server help and its pinned source expose `f16`, `q8_0` and
`q4_0` K/V cache types, explicit `--flash-attn on|off|auto`, logical/physical
batch controls, slot count and continuous batching. These are separate controls
from model-weight Q4/Q8 quantization. The source requires Flash Attention for
quantized V and block-aligned head dimensions. Our head dimension 64 meets the
32-value q8_0/q4_0 block condition; actual support must still be demonstrated by
each profile's startup log and successful request. Preserve unsupported
configuration failures rather than silently switching to f16 or disabling a
requested feature. See [the pinned context validation][context] and
[CLI/API definitions][server].

The completed comparison below reports the observed results without assuming
a universal speed or quality winner.

[metadata]: https://huggingface.co/api/models/bartowski/Llama-3.2-1B-Instruct-GGUF/revision/067b946cf014b7c697f3654f621d577a3e3afd1c?blobs=true
[card]: https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/blob/067b946cf014b7c697f3654f621d577a3e3afd1c/README.md
[context]: https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/src/llama-context.cpp
[server]: https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/tools/server/README.md

## Measured comparison — 2026-11-10

The live Q8 arm loaded, streamed successfully, and answered the fixed arithmetic
case correctly. Both model arms completed all eight performance requests
(two prompts, one warm-up plus three measured repeats) and eight quality cases.
The actual metadata/hash verification and real responses are saved in the
[curated run](../benchmarks/results/2026-11-10-phase5/README.md).

| Arm | Short median TTFT s | Short mean native decode tok/s | Long median latency s | Long peak RSS MiB | Toy exact-match checks |
| --- | ---: | ---: | ---: | ---: | ---: |
| Q4_K_M |0.421|21.19|3.935|1366.5|4/8|
| Q8_0 |0.802|11.30|6.801|1408.1|5/8|

All performance outputs reach the same32-token cap. Three repeats and one fixed
toy quality set support a narrow observation, not a universal ranking or broad
quality-equivalence claim. The original four-thread control's later decode rate
changed by13.5%, and file size does not equal measured RSS. See
[findings and decisions](16-performance-findings.md) for control drift, actual
outputs, limitations and the decision to retain active Q4 defaults.
