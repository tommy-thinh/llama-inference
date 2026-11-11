# Repository guidance

- Keep the scope focused on CPU-only Llama 3.2 1B Instruct inference on a
  self-hosted VPS, with compatible local Linux operation. Read `README.md` and
  the relevant guide in `docs/` before changing behavior.
- Preserve the active Q4_K_M artifact and pinned CPU llama.cpp revision.
  Experimental Q8, KV-cache, prefix-cache, and batching settings belong in
  explicit benchmark configurations. Verify artifact bytes and metadata.
- Keep native inference and PostgreSQL on loopback. Public traffic goes through
  Caddy. Public exposure, disruptive system changes, or production data changes
  require explicit authorization.
- Use one backend worker/instance for admission control and recovery. Read
  `docs/architecture.md`, `docs/deployment.md`, and `docs/operations.md` before
  changing security, persistence, proxy trust, services, or recovery behavior.
- Use the `llama` Conda environment with Python 3.11. `environment.yml` is the
  dependency source of truth; prefer conda-forge and keep dependencies small.
  Assume the user's environment is active and show direct Python/Ruff/pytest
  commands. Agent shells may use `conda run -n llama`.
- Keep model weights, builds, environments, credentials, caches, and transient
  results out of Git. Never print credentials. Respect the Llama license and
  manual access terms; never accept terms on another person's behalf.
- Keep curated raw benchmark evidence immutable. Record actual configurations,
  hardware, checksums, warm-ups, repeated measurements, errors, and limitations.
  Follow `docs/benchmarks.md`; do not invent or relabel measurements.
- For implementation changes, use an independent tester who derives behavior
  from documentation and diagrams before reading implementation. Discuss any
  failed test's input, expectation, source, and cause before changing it.
  Never weaken a regression simply to make it pass.
- Use GPT-6.1-Terra with high reasoning for delegated work. Keep code review
  independent from implementation and test editing.
- Run `ruff check .`, `ruff format --check .`, and `pytest`. Report skips.
  Real inference requires `--run-inference`; database integration additionally
  requires `--run-database` and a dedicated `LLAMA_TEST_DATABASE_URL` ending in
  `_test`. Tests must never use application data or credentials.
- Apply schema changes explicitly with Alembic. Retention defaults to dry run;
  `--apply` deletes data. Manage temporary servers with bounded startup and
  guaranteed shutdown. Preserve existing services and persistent data.
