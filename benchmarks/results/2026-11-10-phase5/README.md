# Phase 5 local CPU experiments — 2026-11-10

Model: exact Meta Llama3.2 1B Instruct; pinned CPU llama.cpp; 6-vCPU Xeon VPS.
No deployment occurred. Active Q4/default serving configuration is unchanged.

- `native.json`:18 profiles,232 successful requests (58 warm-up/174 measured),16
  separate quality outputs. `native-report/` has tables, JSON summaries, charts.
- `prefix.json`: corrected fresh baseline/cache pair;16 successful requests,
 8 duplicate quality cases. Both first warm-ups have zero cached tokens.
  `prefix-report/` contains summaries/charts. The first sweep's long cache warm-up
  had30 shared-header cached tokens; it is preserved, not called fully cold.
- `allocation.json` and `allocation-startup.txt`:6 untimed verbosity4 startup
  probes recording actual KV buffer allocation, plus model/compute trace.
- `stack-sandbox-failed.json`: initial socket-restricted failure before startup,
  retained as an environmental failure rather than a measurement.
- `stack.json`: certificate-verified loopback HTTPS to Caddy/backend/PostgreSQL/
  model,24 waves/56 attempts.42 measured:27 successes,15 expected503 admissions,
 0 other errors. `stack-report/` contains summaries/charts.
- `q8-source-metadata.json`, `q8-verification.json`, and
  `../../artifacts.lock.json`: trusted provenance, checksum, size and GGUF checks.
- `source/*.py.txt`: frozen source used by the initial native sweep, byte-for-byte,
  stored as text evidence rather than imported/executed code. The final runner
  contains later validation/failure/bookkeeping/cache-control hardening; see
  `verification.json` for current hashes and execution/correction notes.

Native repeats use temperature0, seed42,32 output tokens and fixed51/310-token
short/long prompts. Warm-ups never enter summaries. Native concurrency keeps2048
context **per slot**;4 slots therefore allocate8192 total. Quality cases are a
toy exact-match set, not an intelligence benchmark. All process CPU percentages
are relative to one logical CPU; stack counter windows are approximate and can
extend beyond worker completion. Experiments ran without systemd CPU ceilings.

Do not compare tiny differences as causal wins: the final four-thread control
changed by13.5% in short decode speed and one six-thread long request was an
outlier. See [performance findings](../../../docs/16-performance-findings.md)
and [methodology](../../../docs/13-benchmark-methodology.md) for interpretation,
limitations, and reproduction. Full Phase5 test results are separately curated
under `docs/evidence/phase5-*` and linked from project status.
