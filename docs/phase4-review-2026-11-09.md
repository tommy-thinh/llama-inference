What this change does: It prepares a public deployment profile with same-origin
request checks, bounded rate limits, durable daily quotas, and one active
generation with two waiting requests. It also supplies Caddy/systemd templates,
model notices, and manual backup/recovery instructions; it does not deploy them.

Independent read-only review, 2026-11-09. The reviewer used `ponytail-review`
against the uncommitted Phase 4 changes and their callers, with `PROJECT_SPEC.md`,
`AGENTS.md`, and docs 09–11 as the requirements. Expected load is one backend
worker on a trusted single-tenant VPS, direct clients connecting to Caddy, one
CPU inference slot, and a two-request FIFO queue. Multiple backend instances or
an additional proxy are outside the documented contract.

## Findings resolved during review

1. **The backend would stop after 10,000 requests** (`backend/__main__.py:16`,
   `deploy/systemd/llama-backend.service:17`).
   - **What this is:** Uvicorn can stop after a configured request count. The
     prepared unit restarts processes only after failure.
   - **Problem:** The original launcher set `limit_max_requests=10000`. Uvicorn's
     installed source exits normally at that limit, so systemd would leave the
     demo stopped after ordinary traffic reached it.
   - **Fix:** The implementer removed the unnecessary request-count exit. The
     independent launcher regression checks that no such exit is configured.
   - **If we skip it:** Normal traffic eventually takes the demo offline until
     an operator manually restarts it. This finding is fixed in the reviewed code.

2. **Readiness could report success without the quota schema**
   (`backend/database.py:61`, `backend/app.py:97`).
   - **What this is:** Readiness must verify that the public backend can reach
     its database and native inference process.
   - **Problem:** The original database probe used only `SELECT 1`. A database
     still at revision `0001` could report ready while every public generation
     failed because `daily_quotas` did not exist.
   - **Fix:** The probe now selects a quota-table column. This checks the required
     table and runtime SELECT privilege without granting access to Alembic's
     migration table. The independent regression removes/reinstates revision
     `0002` only in its isolated test schema.
   - **If we skip it:** An operator can mistake an unmigrated deployment for a
     working demo. The implementation is fixed; the final verification records
     the database regression result.

## Checks

- Traced the public Host/Origin/Fetch Metadata checks, Secure cookie settings,
  disabled legacy inference/docs routes, forwarding-header trust boundary,
  canonical IP handling, bounded body buffering, and safe error responses.
- Checked bounded fixed-window state, atomic PostgreSQL quota increments and
  rollback, UTC day selection, conservative no-refund charging, and retention.
- Traced queue ordering, saturation, waiter cancellation, permit handoff races,
  upstream closure, durable finalization, and the shared cleanup deadline.
- Checked application log fields, generated request IDs, redaction, and disabled
  Uvicorn raw-path access logging. The pinned native server's ordinary INFO paths
  log task/token statistics; request-body dumps are DEBUG paths and its request
  logger is disabled. Private journals and no verbose prompt logging remain
  explicit operator requirements.
- Checked Caddy's file/path allowlist, overwritten client IP, immediate SSE
  flushing, disconnect propagation, bounded proxy deadlines, and security headers.
- Traced the proposed service users, root-owned deployment copy, copied runtime
  `.git` and shared libraries, scoped Git trust, model read permissions, loopback
  bindings, resource ceilings, and shutdown behavior.
- Read backup publication and restore commands: private credentials/files,
  checked custom archives, publication without overwriting, fresh-target restore,
  explicit grants, and rollback preserving the original database. Backup listing
  is correctly distinguished from a successful restore and power-loss durability.
- Verified that the license and use-policy files match their recorded SHA-256
  values. The interface displays “Built with Llama” and links the agreement,
  policy, and notice. Existing model pins and acceptance evidence are preserved.

The reviewer directly ran the security suite after the launcher correction:
**38 passed, 0 skipped** in 3.23 seconds. An earlier combined ordinary security /
public persistence run passed **39 tests and skipped 14 PostgreSQL cases**;
those skips were explicit because that command did not opt into database tests.
The independent tester owns the complete PostgreSQL, inference, deployment, and
full-suite execution results recorded separately in the final evidence files.

No open must-fix or should-fix findings remain in the reviewed preparation.

Verdict: Ship the local preparation. Public deployment remains deferred.

Not checked: public DNS, public ACME issuance and firewall behavior, installation
or execution of the prepared systemd units, production database migration or
restore, and full-machine disaster recovery. These require Tommy's later
deployment decision and cannot be claimed from repository preparation.
