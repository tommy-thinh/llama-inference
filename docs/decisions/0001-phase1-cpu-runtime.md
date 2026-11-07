# ADR 0001 — Pinned native CPU inference for Phase 1

Status: historical Qwen model decision; the runtime pin remains active, while the
model choice is superseded by [ADR 0002](0002-llama-model-migration.md). Date:
2026-10-21.

## Problem

We need a reproducible, inspectable inference foundation on a six-vCPU, 16 GB VPS
without introducing GPU dependencies or a web application before model serving
has been verified.

## Options

- Native PyTorch/Transformers: flexible for research, with a larger inference
  dependency stack and less direct access to the selected GGUF CPU kernels.
- A higher-level model manager: convenient installation, with additional behavior
  and lifecycle conventions to account for when measuring this runtime.
- llama.cpp built from pinned source: direct CPU/GGUF support and a local HTTP
  server, with the cost of a native build and explicit artifact compatibility checks.

## Decision and evidence

Use llama.cpp v0.7.0 at `620d2225cc436ab0b001e3200e590e3b286ef9ab`. Build the
server with Release/native CPU settings and no GPU, UI, BLAS, or HTTPS dependencies.
Pin Bartowski's Q4_K_M conversion of the exact post-trained Qwen3.5-0.8B model;
verify its size and published SHA-256. The llama.cpp organization's inspected
GGUF repository had Q4_0/Q8_0/BF16 artifacts; the selected community publisher
provides the preferred Q4_K_M variant with a declared source model and checksum.
This is not a different model or an unapproved stack change.

Check actual GGUF architecture, tokenizer, template, and MTP layout, then verify
real generation and streaming. Maintain a single local slot and moderate context,
disable thinking and request-prefix reuse for the baseline, and use explicit
generation limits in clients. Four threads is the initial operational default;
the baseline compares two and six before any broader tuning.

Use foreground execution for manual work and owned subprocesses with readiness
deadlines/finally cleanup for automated work. System service installation remains
in Phase 4. Use stdlib plus HTTPX for measurement and pytest/Ruff for verification;
there is no need for PyTorch, Transformers, Node.js, or a database yet.

## Trade-offs and reconsideration

Q4_K_M can lose quality relative to higher precision, and a smaller artifact may
not decode faster. Native binaries depend on the target CPU. Release pinning trades
automatic updates for repeatability. The community conversion adds a provenance
dependency; checksum validation proves byte identity rather than quality.

One slot simplifies the baseline but limits parallel service capacity. Disabled
prefix reuse makes repeated prefill comparable and leaves a useful optimization
unmeasured. Revisit quantization, state/cache policies, and threads with Phase 5
evidence. Revisit lifecycle management at deployment. Changing the model, exposing
the service publicly, or implementing the next phase requires explicit review.
