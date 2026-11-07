# Project instructions

Tommy is an undergraduate CS student learning AI systems engineering. Build
intuition before details, explain why decisions work, and use concrete examples.
Keep chat updates concise; put detailed teaching material in `docs/`.

- Follow `PROJECT_SPEC.md`. Implement only the phase explicitly authorized.
- Phase 1 implementation and baseline are complete. Finish review/audit and
  commit, then implement authorized Phase 2.
  Tommy authorized this sequence on 2026-11-08. Stop before Phase 3.
- Read `docs/project-status.md` before continuing work in a later session.
- Use CPU-only llama.cpp and the exact Meta Llama 3.2 1B Instruct model in the
  active configuration; never substitute silently. Keep the former Qwen model
  and benchmark results as labeled historical data.
- Respect the Llama 3.2 Community License and manual model access. Do not accept
  terms on Tommy's behalf or download/use weights before he confirms acceptance.
- Keep inference on `127.0.0.1`. Public exposure and disruptive system changes
  require approval. Preserve existing services, configurations, and persistent data.
- Keep dependencies small. Use the `llama` Conda environment (Python 3.11),
  HTTPX, pytest, and Ruff for Phase 1 Python work. Keep `environment.yml` as the
  dependency source of truth and use conda-forge packages where available.
- Keep model weights, builds, caches, environments, and transient results out of Git.
- Pin runtime/model revisions, verify checksums, and record actual measurements.
- Separate ordinary tests from opt-in real inference tests. Always report skips.
- Assume Tommy activates the environment with `conda activate llama`. Show
  `python`, `pytest`, and `ruff` commands directly. If an agent's tool shell is
  not activated, use `conda run -n llama` for project commands.
- Run `ruff check .`, `ruff format --check .`, and `pytest`.
- Run real inference tests with `pytest --run-inference` after setup.
- For implementation changes, delegate test design and test changes to an
  independent tester subagent. Have it derive expected behavior from the spec,
  documentation, and any architecture/class diagrams before reading implementation.
  Do not give it the implementation author's reasoning as its test oracle.
- Use a separate subagent with `ponytail-review` when a code review is requested.
  The reviewer reports findings without editing production code or tests.
- When independent tests fail, the tester must report the input, expectation,
  requirements source, and failure to the implementer/reviewer. Discuss whether
  it is a code defect, test defect, environment issue, or documented limit before
  reaching a conclusion. Do not weaken tests just to make them pass.
- Manage test/benchmark servers with bounded startup and guaranteed shutdown.
- Update the project status after each milestone. Do not claim unverified success.
- Tommy confirmed model terms on 2026-11-08; the pinned local GGUF passed setup
  verification. Preserve the attestation and verification record. Changes to model
  bytes or pins require fresh checksum, metadata, and live-inference checks.
