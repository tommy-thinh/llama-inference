# Task: Migrate Phase 1 from Qwen3.5-0.8B to Llama 3.2 1B Instruct

We have decided to change the model used in this project from **Qwen3.5-0.8B** to **Meta Llama 3.2 1B Instruct**.

The primary objective remains unchanged: build a **Resource-Constrained Efficient LLM Inference Platform** on a CPU-only VPS.

This is a model migration, **not a complete project rewrite**.

## 1. New model configuration

- **Model:** Meta Llama 3.2 1B Instruct
- **Official model:** [https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct)
- **Inference engine:** llama.cpp / llama-server
- **Model format:** GGUF
- **Initial quantization:** Q4_K_M, if compatible
- **Target hardware:** 6 vCPU, 16 GB RAM, 100 GB NVMe, CPU-only
- **Operating system:** Ubuntu 26.04 LTS

Use a trusted GGUF conversion and verify its provenance, compatibility, filename, and chat template before downloading.

Be aware that Llama 3.2 uses Meta's Community License, not Apache 2.0. Verify applicable access requirements, license conditions, and attribution requirements before proceeding with any public deployment.

## 2. Migration tasks

### A. Inspect the existing project

First, inspect the repository and current Phase 1 implementation.

Identify:

- Qwen-specific code and configuration.
- Model paths, model identifiers, and download scripts.
- llama.cpp build and execution commands.
- Model-specific prompt templates and tokenizer assumptions.
- Existing benchmarks and test cases.
- Documentation referencing Qwen.
- Any Phase 1 work that can be reused.

Preserve working, model-independent components.

Do not restart the project from scratch.

### B. Replace Qwen with Llama

1. Replace Qwen3.5-0.8B with Llama 3.2 1B Instruct throughout the project.
2. Download or configure an appropriate GGUF model artifact.
3. Update llama-server configuration and startup scripts.
4. Verify the correct Llama 3.2 chat template and special tokens.
5. Configure CPU threads, context size, and generation parameters.
6. Ensure the inference server loads the model only once.
7. Verify successful inference and streaming.
8. Remove obsolete Qwen-specific configuration and dependencies when safe.

Do not delete existing Qwen weights or benchmark artifacts without explicit approval.

### C. Update Phase 1 benchmarks

Run a fresh inference baseline using Llama 3.2 1B Instruct.

Measure:

- Model size
- Quantization format
- Memory consumption
- CPU utilization
- Time to first token (TTFT)
- Generation speed (tokens/second)
- Request latency
- Performance under different CPU thread counts
- Performance under different context lengths

Record actual measurements and exact test configurations.

Do not reuse Qwen benchmark results as if they were Llama measurements.

Preserve any existing Qwen measurements as clearly labeled historical results.

### D. Prepare for Efficient AI experiments

The long-term priority is now **Efficient AI and inference optimization**, particularly:

1. Model quantization (Q4 vs Q8).
2. KV cache memory usage and quantization.
3. Prefix caching and prompt reuse.
4. Context length versus memory and latency.
5. CPU threading and inference optimization.
6. Batching, concurrency, and throughput.
7. Latency, memory, and output-quality trade-offs.

Llama 3.2 uses a Transformer architecture with Grouped-Query Attention (GQA), making it useful for studying conventional KV cache behavior.

Investigate the actual caching and quantization features supported by our installed llama.cpp version. Do not assume every optimization is available or beneficial.

Do not implement the full optimization suite yet. These are planned experiments for Phase 5.

### E. Update automated tests

Review and update all tests affected by the model migration.

At minimum, verify:

- Model file availability and integrity.
- llama-server startup.
- Model loading.
- Successful text generation.
- Correct chat-template behavior.
- Streaming generation.
- Invalid request handling.
- Benchmark script functionality.

Run the relevant tests and report actual outcomes.

Do not claim tests passed unless they were executed.

### F. Update documentation and project instructions

Update all relevant documentation, including:

- `README.md`
- `AGENTS.md`
- PROJECT-SPEC.md
- `docs/project-status.md`
- Phase 1 inference documentation
- Baseline benchmark documentation
- Architecture diagrams
- Model configuration examples
- Future implementation roadmap

Search the repository for stale Qwen references.

Replace references that describe the current or planned model. Preserve explicitly historical benchmark records and migration notes.

## 3. Preserve the six-phase roadmap

The six phases remain:

1. **Inference Foundation** — Llama 3.2 1B CPU inference and baseline benchmarking.
2. **FastAPI Backend and Web Frontend** — Streaming API and vanilla JavaScript UI.
3. **PostgreSQL and Persistence** — Sessions, conversation history, and metrics.
4. **Public Deployment and Security** — Caddy, HTTPS, rate limiting, queueing, and resource controls.
5. **Efficient AI Benchmarking and Optimization** — Quantization, KV cache, CPU optimization, caching, concurrency, and performance experiments.
6. **Dockerization and Portfolio** — Docker Compose, reproducibility, architecture documentation, and engineering presentation.

All other architectural decisions remain unchanged unless a model-specific compatibility issue requires adjustment.

## 4. Execution instructions

**Start the migration immediately. Do not wait for my approval.**

Follow this workflow:

1. Inspect the existing repository.
2. Identify the changes required.
3. Implement the migration.
4. Run tests.
5. Run real Llama inference and baseline benchmarks.
6. Update documentation.
7. Verify the migration is complete.
8. Summarize the results.

Make reasonable technical decisions independently.

Do not interrupt the migration for routine implementation details.

However, request approval before destructive operations, potentially disruptive system changes, exposing services publicly, or major architectural deviations.

If model access requires my acceptance of Meta's license or another manual action, explain the blocker and continue with independent tasks that can safely be completed.

## 5. Completion criteria

Consider the migration complete only when:

- Llama 3.2 1B Instruct replaces Qwen as the project's configured model.
- llama-server successfully loads the selected Llama model.
- Local inference and streaming work.
- Updated tests pass.
- New baseline benchmarks are recorded.
- Documentation consistently reflects Llama.
- `docs/project-status.md` accurately records the project's current state.
- The six-phase roadmap is updated without losing previous progress.

## 6. Final report

When finished, provide:

1. **Migration Summary:** What changed and why.
2. **Files Modified:** Important code, configuration, and documentation changes.
3. **Tests:** Actual commands executed and their outcomes.
4. **Benchmark Results:** Real Llama measurements, clearly labeled.
5. **Documentation:** What was updated and where to read it.
6. **Known Limitations:** Remaining problems or uncertainties.
7. **Next Steps:** Any unfinished Phase 1 work and the planned Phase 2.

**IMPORTANT: Complete the migration and any unfinished Phase 1 work, then STOP. Do not start Phase 2 without my approval.**

The final outcome should be a working, tested, documented **Llama 3.2 1B Instruct inference foundation**, ready for the next development phase.
