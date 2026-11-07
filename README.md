# Resource-constrained Llama inference

A learning and engineering project for serving **Meta Llama 3.2 1B Instruct**
with llama.cpp on a CPU-only VPS. Phase 1 preserves the working CPU runtime,
local-only server, process management, and benchmark runner while migrating the
active model from Qwen to a pinned Llama Q4_K_M GGUF.

## Model access status

The model page is manually gated and the model uses Meta's Llama 3.2 Community
License. Tommy confirmed acceptance on 2026-11-08. The pinned Llama weights are
downloaded in `.runtime/models/` and pass checksum/GGUF verification. Real
generation, streaming, and template/tokenization tests pass. The full
Llama baseline is recorded in [the benchmark guide](docs/03-baseline-benchmark.md). Existing Qwen results are historical.

Before downloading, review and accept the terms on the
[official model page](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct)
and read the [Community License](https://dev.meta.ai/llama/llama3_2/license).
Then, from the repository root:

```bash
python -m inference.setup --jobs 3 --accept-llama-license
python -m inference.setup --verify-only
```

The flag confirms that you personally accepted the terms; it does not accept
them for you. The selected artifact, converter revision, filename, expected
size, SHA-256, and metadata checks are pinned in
[`inference/artifacts.lock.json`](inference/artifacts.lock.json).

## Python environment

Miniconda manages the project Python environment. Create and activate it from
the repository root:

```bash
conda env create -f environment.yml
conda activate llama
```

The file uses only `conda-forge` (`nodefaults` excludes configured default
channels) and pins Python 3.11, HTTPX 0.28.1, pytest 9.1.1, and Ruff 0.17.1.
These packages are available from Conda, so no pip-only dependency is needed.
The `nodefaults` channel entry is documented in
[Conda's environment-file guide](https://docs.conda.io/projects/conda/en/stable/user-guide/tasks/manage-environments.html).

This VPS's Conda terms plugin checks globally configured default channels before
processing an environment file. Since those terms have not been accepted, the
tested install used explicit per-command channel overrides instead:

```bash
conda create --override-channels -c conda-forge -n llama python=3.11 -y
conda install --override-channels -c conda-forge -n llama \
  httpx=0.28.1 pytest=9.1.1 ruff=0.17.1 -y
conda activate llama
```

This does not change global Conda settings or accept terms for other channels.

## Run locally after setup

Prerequisites: Linux, Miniconda, Git, CMake, Make, and a C/C++ compiler.
Activate the `llama` environment before running project commands.

```bash
python -m inference.server
```

The foreground server listens at `http://127.0.0.1:8081`; keep that terminal
open and use Ctrl+C to stop it. In a second terminal:

```bash
curl -fsS http://127.0.0.1:8081/health
curl -N --fail-with-body http://127.0.0.1:8081/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama-3.2-1b-instruct","messages":[{"role":"user","content":"Explain CPU cache locality in three sentences."}],"max_tokens":128,"stream":true,"temperature":0}'
```

Weights, builds, and logs stay under ignored `.runtime/`.

## Verify and benchmark

```bash
ruff check .
ruff format --check .
pytest
pytest --run-inference
python -m benchmarks.scripts.run --config benchmarks/configs/smoke.json
python -m benchmarks.scripts.run --config benchmarks/configs/baseline.json
```

Ordinary tests do not load a model. `--run-inference` starts and stops temporary
localhost servers; missing assets fail the explicitly requested live suite.
Benchmarks own their server processes and write new results under ignored
`benchmarks/results/local/`.

## Read and learn

- [Current phase, blocker, and verification](docs/project-status.md)
- [Inference fundamentals and GQA/KV-cache intuition](docs/01-inference-fundamentals.md)
- [Setup, model terms, and local API](docs/02-running-llama.md)
- [Llama baseline status and reproduction plan](docs/03-baseline-benchmark.md)
- [Historical Qwen baseline, preserved separately](docs/03-qwen-historical-benchmark.md)
- [Runtime and model-migration decisions](docs/decisions/0002-llama-model-migration.md)
- [Full scope and six-phase roadmap](PROJECT_SPEC.md)

The six phases still end with Dockerization and portfolio preparation. Tommy has
authorized Phase 2 after the Phase 1 review/audit and commit. See project status
for the current milestone.
