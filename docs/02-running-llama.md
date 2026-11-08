# 02 — Running Llama locally

The inference server owns one loaded model and its state. Local clients send
requests to that process; they do not load weights again for each prompt. Phase 1
keeps the listener on `127.0.0.1` and does not install a public proxy or system
service.

Run Python commands below after `conda activate llama`. Create the environment
from [`environment.yml`](../environment.yml) as shown in the
[README](../README.md#python-environment).

## Selected model and verification status

| Item | Pinned selection |
| --- | --- |
| Upstream model | `meta-llama/Llama-3.2-1B-Instruct` |
| Upstream revision | `9213176726f574b556790deb65791e0c5aa438b6` |
| GGUF publisher | `bartowski/Llama-3.2-1B-Instruct-GGUF` |
| Conversion revision | `067b946cf014b7c697f3654f621d577a3e3afd1c` |
| Converter provenance | Bartowski; model card identifies llama.cpp b3821 and imatrix |
| File | `Llama-3.2-1B-Instruct-Q4_K_M.gguf` |
| Expected size | 807,694,464 bytes |
| Expected SHA-256 | `6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83` |
| Format | GGUF, Q4_K_M |
| Runtime | llama.cpp v0.7.0, commit `620d2225cc436ab0b001e3200e590e3b286ef9ab` |

The converter's pinned card identifies Meta's model as its base, lists the exact
Q4_K_M filename and size, and shows the Llama 3 message markers. The official
model page is manually gated. **Tommy confirmed acceptance on 2026-11-08** and
the pinned weights are now downloaded locally. Setup verified 807,694,464 bytes,
the pinned SHA-256, architecture, quantization, tokenizer, and template metadata.
The template emits BOS through `{{- bos_token }}`; llama.cpp supplies that value
from tokenizer metadata (BOS ID 128000, EOS ID 128009). The verifier accepts this
Jinja form and still requires the other Llama markers and vocabulary tokens.
The existing Qwen weights and results stay in place as historical data.

The expected header checks in `inference/artifacts.lock.json` cover the `llama`
architecture, Q4_K_M file type, layer/embedding and GQA dimensions, BPE
tokenizer, and template markers. `verify_model()` also verifies size and SHA-256
before the server can load the artifact. The source model uses GQA: 32 query
heads share 8 key/value heads. This is why its attention state is a conventional
Transformer KV cache using GQA.

## License and access checkpoint

Llama 3.2 uses Meta's **Community License**, identified as `llama3.2` by both
model cards. Meta lists the upstream Hugging Face model as manually gated. Before
using these weights, review and accept the terms for the official model and read
the Acceptable Use Policy. This repository will not accept those terms on your
behalf. After you have done so, confirm your own acceptance explicitly when
running setup:

```bash
python -m inference.setup --jobs 3 --accept-llama-license
python -m inference.setup --verify-only
```

The setup flag is an attestation from the person running the command; it does
not click through Meta or Hugging Face for you. Setup downloads only the pinned
conversion and verifies it before publishing the final file. If the publisher
requires credentials or the upstream access request is not approved, setup
reports the access error and leaves no partially verified model behind.

Before a public deployment or redistribution, check the current license and
policy again. Use must follow the [Acceptable Use Policy](https://www.llama.com/llama3_2/use-policy).
For a covered product or service, the agreement requires a copy of the agreement
and a prominent “Built with Llama” notice. Distributed model materials must
retain this attribution in a `NOTICE` file: “Llama 3.2 is licensed under the
Llama 3.2 Community License, Copyright © Meta Platforms, Inc. All Rights
Reserved.” The license also requires a separate Meta license if the licensee's
products exceeded 700 million monthly active users on the model's release date.
These are deployment gates, not Apache 2.0 terms.

## Verify the existing runtime without model access

The pinned CPU build already exists in `.runtime/llama.cpp`. It can be checked
without cloning, rebuilding, or reading model weights:

```bash
python -m inference.setup --verify-runtime-only
```

Setup checks the exact source commit and executable version. The native binary
uses shared libraries beside `llama-server`; subprocess commands add that local
directory to `LD_LIBRARY_PATH` without installing libraries system-wide. The
build uses Release/native CPU settings and disables CUDA, Vulkan, BLAS, tests,
examples, and UI. Its CMake options are pinned in
`inference/artifacts.lock.json`.

After license acceptance, full setup is:

```bash
python -m inference.setup --jobs 3 --accept-llama-license
python -m inference.setup --verify-only
```

The first command builds only if the selected pinned server executable is absent;
an existing mismatched executable is preserved and rejected. It downloads the
model to `.runtime/models/`. An existing invalid model is preserved and
reported. A download is written to a temporary `.part` file, checked for exact
size, SHA-256 and GGUF metadata, then renamed to the final filename. Builds,
weights, verification records, and logs are ignored by Git.

## Start and configure the server

```bash
python -m inference.server
```

This is a foreground command. Keep that terminal open and press Ctrl+C to stop
the server. The launcher replaces itself with `llama-server`, so the signal
reaches the serving process directly.

| Setting | Default | Reason |
| --- | ---: | --- |
| Host and port | `127.0.0.1:8081` | Local clients only |
| GPU layers | `0` | CPU-only VPS |
| Inference and batch threads | `4` | Initial allocation on six vCPUs |
| Parallel slots | `1` | One active generation; one model load |
| Context capacity | `4096` tokens | Room for a prompt alongside the 2,048-token output cap |
| Default generation cap | `1024` tokens | Clients may request up to 2,048 output tokens |
| Batch / microbatch | `256` / `128` | Bounded prompt processing |
| KV cache types | f16 K and V | Keep cache precision fixed for baseline |
| Prompt cache RAM / checkpoints | `0` / `0` | Avoid extra retention in the baseline |
| Chat formatting | Embedded template with `--jinja` | Keep Llama formatting with the model |
| Context shift / web UI | Disabled | Explicit limits and no unneeded interface |

The model advertises a larger context than the Phase 1 launcher allows. The
launcher bounds experiment settings to 512–8,192 tokens; Phase 5 can study
larger capacities after measuring their memory and latency costs. CPU threads,
context, output, and port can be overridden:

```bash
python -m inference.server --threads 2 --ctx-size 1024 --max-tokens 64 --print-command
```

`--print-command` does not start the service. Optional environment values are
listed in `.env.example`; a `.env` file is not read automatically. Use
`LLAMA_MODEL_PATH` to point at an already downloaded, verified artifact outside
the checkout. The server strips inherited `LLAMA_ARG_*` variables so they
cannot silently change the experiment or bind address.

The launcher default is not an application security quota. Phase 1 clients
should explicitly pass `max_tokens`; request quotas and admission controls are
planned for later phases. Do not expose this listener publicly.

## Call the local API

Use a second terminal while the first terminal owns the foreground server:

```bash
curl -fsS http://127.0.0.1:8081/health
curl -fsS http://127.0.0.1:8081/v1/models
```

The stable model alias is `llama-3.2-1b-instruct`. A non-streaming request is:

```bash
curl --fail-with-body http://127.0.0.1:8081/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama-3.2-1b-instruct","messages":[{"role":"user","content":"What is 2 + 2? Answer with only the number."}],"temperature":0,"max_tokens":16,"cache_prompt":false}'
```

To watch streaming output arrive progressively:

```bash
curl -N --fail-with-body http://127.0.0.1:8081/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama-3.2-1b-instruct","messages":[{"role":"user","content":"Count from 1 to 10, separated by commas, without explanation."}],"temperature":0,"max_tokens":48,"stream":true,"stream_options":{"include_usage":true},"cache_prompt":false}'
```

`-N` disables curl output buffering. The server emits SSE JSON events containing
visible deltas, usage/timings, a finish reason, and `[DONE]`. `cache_prompt=false`
keeps repeated baseline requests from reusing a previous prefix; it does not
disable the KV cache within each request. An invalid request should return an
HTTP error while `/health` continues to work.

## Tests and process ownership

```bash
ruff check .
ruff format --check .
pytest
pytest --run-inference
```

The ordinary suite uses fixtures and mocked HTTP responses, so it does not load
the model. The opt-in suite starts a temporary owned server and verifies
artifact metadata, health/model listing, arithmetic generation, Llama template
markers, progressive streaming, request-error recovery, and benchmark smoke.
Until the artifact has been accepted, downloaded, and verified, the live suite
is blocked and must fail with a clear missing-asset reason when explicitly run.

Automated servers bind an available loopback port, wait for readiness for at
most 120 seconds, then terminate and reap only the child process they started.
If another process owns a chosen manual port, pick another port; never kill the
unrelated process.

## Supported cache and template features in the pinned runtime

The pinned llama.cpp server documentation says that this release reads the chat
template from GGUF metadata, supports Jinja templates, and has `cache_prompt`
prefix reuse enabled by default. Its options include f16, q8_0, and several
lower-precision K/V cache types, as well as cache RAM limits, idle-slot caching,
context checkpoints, and KV cache reuse. Phase 1 explicitly disables cross-
request prompt reuse and extra cache storage; it keeps f16 K/V. The presence of
an option is not evidence that it helps this model on this VPS. Controlled
comparisons belong to Phase 5.

## References

- [Official gated model page and model access](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct)
- [Meta Llama 3.2 model card and prompt format](https://github.com/meta-llama/llama-models/blob/main/models/llama3_2/MODEL_CARD.md)
- [Meta Llama 3.2 Community License](https://dev.meta.ai/llama/llama3_2/license)
- [Bartowski conversion card, pinned revision](https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/tree/067b946cf014b7c697f3654f621d577a3e3afd1c)
- [Pinned llama.cpp server options](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/tools/server/README.md)

Continue with [the Llama baseline plan](03-baseline-benchmark.md) and the
[learning checkpoint](01-inference-fundamentals.md#what-you-should-understand-after-this-phase).
