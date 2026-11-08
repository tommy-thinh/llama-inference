# ADR 0002 — Migrate the Phase 1 model to Llama 3.2 1B Instruct

Status: selected; model acquired and metadata verified on 2026-11-08 after Tommy
confirmed license/access acceptance. See project status for live-test outcomes.
Date: 2026-10-25.

Output-limit update: Tommy subsequently requested a default of 1,024 output
tokens and a maximum of 2,048. The current launcher uses a 4,096-token context
to accommodate prompt and output; the original baseline retains its recorded
profile configurations.

## Context

The project remains a CPU-only llama.cpp inference platform on the six-vCPU VPS.
This decision changes the model selection only. The pinned llama.cpp v0.7.0
runtime, one-slot local server, Python benchmark runner, and six-phase roadmap
remain in place. The previous Qwen weights and curated measurements remain
historical and must not be overwritten or presented as Llama results.

## Decision

Select Meta Llama 3.2 1B Instruct in GGUF Q4_K_M format from Bartowski's
`Llama-3.2-1B-Instruct-GGUF` conversion. The immutable conversion revision,
filename, expected size and SHA-256, metadata checks, and license identifiers
are in `inference/artifacts.lock.json`. The converter card identifies the
upstream Meta model and llama.cpp b3821 imatrix quantization provenance.

Use the Llama 3 message template embedded in the GGUF. Expected markers include
`<|begin_of_text|>`, `<|start_header_id|>`, `<|end_header_id|>`, and
`<|eot_id|>`. Keep llama.cpp's Jinja support enabled and verify both the GGUF
metadata and `/apply-template` output before claiming runtime compatibility.

Continue to bind `127.0.0.1`, set zero GPU layers, use four threads and a 2,048
token initial context, and load one model in one server process. Keep f16 K/V
cache and cross-request prompt reuse disabled for the baseline; cache format,
prefix reuse, concurrency, and Q4-vs-Q8 comparisons are Phase 5 experiments.

## Access and license

The official Hugging Face model is manually gated. Llama 3.2 is governed by
Meta's Community License and Acceptable Use Policy, not Apache 2.0. Setup refuses
to download until the person running it has accepted the terms and explicitly
passes `--accept-llama-license`. Tommy confirmed acceptance on 2026-11-08 before
the pinned weights were downloaded and verified in this workspace.

Before a public deployment or redistribution, review the terms again. The
agreement requires a copy alongside covered materials and prominent “Built with
Llama” display for covered products/services; distributed model materials must
retain Meta's attribution in a `NOTICE` file. It also has an additional license
condition for products that exceeded 700 million monthly active users on the
release date. This project has not begun public deployment.

## Consequences

- The official Llama model's GQA provides conventional Transformer KV cache
  behavior to study in Phase 5.
- A pinned community conversion adds a publisher/provenance dependency; checksum
  validation proves byte identity, not model quality or license compliance.
- Until manual acceptance and local verification, code/config and test work can
  be completed, but live inference and performance claims remain blocked.
- Model access and the associated license conditions become a prerequisite for
  completing Phase 1. The Phase 2 gate remains closed.
