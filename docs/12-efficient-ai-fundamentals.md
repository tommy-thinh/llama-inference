# 12 — Efficient AI fundamentals

An inference server repeatedly transforms tokens into vectors, moves model weights
through memory, and computes the next token. Optimizing it means finding which of
those costs dominates this machine and workload. A smaller model file alone does
not establish lower latency. Phase 5 tests the hypotheses below on the pinned
CPU-only Llama 3.2 1B Instruct runtime; measured findings belong in
[16 — Performance findings](16-performance-findings.md).

## Prefill and decoding do different work

During **prefill**, the runtime processes the existing prompt, potentially many
tokens together. During **decoding**, it generates the next token, appends its
state, and repeats. A 500-token prompt followed by a 20-token answer therefore
contains a prompt-processing phase and 20 dependent generation steps. Increasing
the prompt batch size can help the first phase without accelerating every
generation step.

Time to first token (TTFT) includes admission/queue delay, prompt processing,
sampling and delivery of the first visible text. It is not just prefill time.
End-to-end latency includes the rest of decoding and streaming. Native runtime
`prompt_ms` and `predicted_ms` are useful narrower measurements, but subtracting
them from client latency leaves several combined costs: scheduling, HTTP,
framework, persistence and streaming. That residual cannot identify precise
network time. The [pinned server timing documentation][server] defines the native
counters used in these experiments.

## Weight precision trades representation against work

A floating-point weight has many representable values. Quantization replaces a
group of weights with smaller integer codes plus scales or related metadata.
For intuition, encoding weights near `[-1, 1]` with only a few levels forces
nearby original values onto the same value. That saves bytes but introduces
rounding error; tensor sensitivity and the quantizer's calibration determine how
much that changes an answer.

The file label describes a quantization recipe, not an exact number of bits for
every model value. `Q4_K_M` mixes tensor precisions; the pinned quantizer contains
rules that retain selected tensors as `Q6_K`. GGUF also contains metadata,
tokenizer data and other tensors. `Q8_0` uses blocks of 32 signed 8-bit values
and a 16-bit scale: **34 bytes per block**, or 8.5 bits per represented value.
`q4_0` blocks contain 32 four-bit values plus a 16-bit scale: **18 bytes**, or
4.5 bits per value. These block sizes come directly from the
[pinned GGML definitions][blocks]; mixed `Q4_K_M` weight storage is a different
format from `q4_0` KV storage. See the [quantizer's tensor selection][quantizer].

On a memory-constrained CPU, fewer weight bytes may reduce memory traffic.
However, unpacking quantized blocks, SIMD kernel support, cache locality and
thread scheduling also cost time. Q8 can therefore beat Q4 on a particular
operation even though its file is larger. Measure both prompt processing and
decoding, and evaluate the same answers before making a recommendation.

## KV caching avoids recomputing previous tokens

Attention uses query, key and value vectors. Once an earlier token's key and
value are computed, decoding can store them instead of recomputing the entire
prefix for each new token. This **KV cache** belongs to a request/slot's inference
state. It is separate from the model weights, the operating system's file cache,
and llama.cpp's optional RAM cache of saved idle prompt states.

Llama 3.2 1B has 16 layers, 32 query heads, 8 KV heads and head dimension 64,
verified from the pinned GGUF. Four query heads share each KV head. This is
grouped-query attention (GQA); using 32 query heads in the cache calculation
would overestimate its logical KV elements by four times. The
[GQA paper][gqa] explains this head-sharing design.

For one sequence with allocated context `C`, the unquantized logical size is:

```text
layers × C × KV_heads × head_dimension × 2(K and V) × bytes_per_element
= 16 × C × 8 × 64 × 2 × 2 bytes for f16
= C × 32 KiB
```

| Context tokens | f16 K+V logical size | q8_0 K+V blocks | q4_0 K+V blocks |
| ---: | ---: | ---: | ---: |
| 1,024 | 32 MiB | 17 MiB | 9 MiB |
| 2,048 | 64 MiB | 34 MiB | 18 MiB |
| 4,096 | 128 MiB | 68 MiB | 36 MiB |

The quantized columns include 32-value block scales. They assume each head's
64 values form complete blocks, equal K/V precision and one sequence. They are
derived expectations, **not measured allocations**. Runtime padding, unified
cache layout, multiple slots, scratch buffers and weights all affect allocation
or process memory. Inspect the native KV allocation log and process RSS
separately. Doubling configured context can allocate more cache even when the
actual prompt is unchanged.

The [pinned context validation][context] requires Flash Attention for quantized
V and checks head dimensions against quantization block size. Our 64-value head
dimension meets the 32-value block condition. A valid dimension is necessary but
does not prove every runtime/backend combination works; failed configurations
remain recorded failures.

## Prefix reuse and Flash Attention address different costs

Request-level `cache_prompt=true` asks llama.cpp to reuse an existing matching
prefix. For example, a long repeated instruction followed by a different
question may avoid recomputing the instruction. Native `cache_n` counts reused
prompt tokens and `prompt_n` counts newly processed prompt tokens. A short TTFT
alone cannot prove a hit. A warm repeated-prefix run describes reuse and cannot
be presented as cold prompt processing. The [server API][server] also warns that
different batch shapes can change floating-point results when prompt reuse is
enabled. Application requests retain `cache_prompt=false`: sharing prompt state
across unrelated sessions raises privacy and isolation concerns.

Flash Attention reorganizes attention to reduce intermediate memory traffic
without replacing exact attention with an approximation. The original
[FlashAttention paper][flash] evaluates GPU memory hierarchies; its reported GPU
speedups are not predictions for this CPU. This pinned build has a CPU Flash
Attention operation, and Phase 5 compares its actual on/off behavior with f16 KV
before considering speed claims. Quantized V uses Flash Attention as a runtime
requirement, so turning both controls at once would confound an isolated
on/off comparison.

## Batching can improve throughput while increasing waiting

`--batch-size` sets the logical maximum tokens in a batch;
`--ubatch-size` bounds physical microbatches. They are token limits rather than
HTTP-client counts. `--parallel` controls native server slots, while continuous
batching lets active sequences contribute work to later batches. More clients
can improve aggregate tokens/second while worsening each client's TTFT or
latency. Holding total context constant while increasing slot count can also
reduce usable context per slot, so the experiment keeps context **per slot**
constant and records the total configured context. These controls are defined
by the [pinned server CLI][server].

The application's one-active FIFO queue is another layer. It protects the model
from excessive simultaneous work but can make queued requests slow. Immediate
rejection removes waiting for rejected clients, which does not make those
requests successful. Compare successful throughput, all-attempt rejection/error
rates and latency together. A p95 from a few controlled waves describes those
samples; it does not establish public-service capacity.

[server]: https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/tools/server/README.md
[blocks]: https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/ggml/src/ggml-common.h
[quantizer]: https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/src/llama-quant.cpp
[context]: https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/src/llama-context.cpp
[gqa]: https://arxiv.org/abs/2305.13245
[flash]: https://arxiv.org/abs/2205.14135
