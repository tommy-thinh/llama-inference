# Project instructions

Tommy is an undergraduate CS student learning AI systems engineering. Build
intuition before details, explain why decisions work, and use concrete examples.
Keep chat updates concise; put detailed teaching material in `docs/`.

- Follow `PROJECT_SPEC.md`. Implement only the phase explicitly authorized.
- Phases 1–4 are committed as `de5b6b5`, `e9afd64`, `bbae986`, and `8e0425b`.
  Tommy authorized Phase 5 on 2026-11-10 as the final implementation milestone.
  Dockerization/Phase 6 is removed from current scope. Run real optimization
  experiments and test the whole stack locally; **do not deploy**. Preserve both
  local-running and manual deployment instructions for an engineering/recruiter audience.
- Before Phase 5 benchmark changes, read `docs/13-benchmark-methodology.md`.
  Preserve active Q4 defaults, verify additional model artifacts, save real raw
  measurements, and isolate full-stack experiments in a dedicated `_test` database.
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
- Phase 3 uses PostgreSQL 18, SQLAlchemy 2, Alembic, and psycopg 3. Apply schema
  changes explicitly with Alembic; run only one backend worker. Database tests
  require `--run-database` and `LLAMA_TEST_DATABASE_URL` for a dedicated `_test`
  database, with isolated schemas. Never run tests against application data.
- Local credentials are in ignored `.runtime/database.env` (mode 0600). Never
  print or commit them. Retention defaults to dry run; `--apply` deletes rows.
- Before public-profile, proxy, service, quota, or recovery changes, read
  `docs/09-deployment-guide.md`, `docs/10-security-and-resource-management.md`,
  and `docs/11-operations-and-troubleshooting.md`. Public mode requires one worker,
  Secure cookies, an exact HTTPS origin, and the explicitly migrated quota schema.
- For implementation changes, delegate test design and test changes to an
  independent tester subagent. Have it derive expected behavior from the spec,
  documentation, and any architecture/class diagrams before reading implementation.
  Do not give it the implementation author's reasoning as its test oracle.
- Tommy requires all subagents to use GPT-6.1-Terra with high reasoning from
  2026-11-10 onward. Use that explicit model/effort for delegated work.
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
