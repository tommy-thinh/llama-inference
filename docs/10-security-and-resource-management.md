# 10 — Phase 4 security and resource contract

Tommy authorized preparation on 2026-11-09 and explicitly deferred deployment.
All public configuration is opt-in through `LLAMA_PUBLIC_ORIGIN=https://YOUR_DOMAIN`.
A public profile requires a database and Secure cookies; the local profile remains
usable over HTTP. One backend worker/instance owns the admission queue and limiter.

## Public request boundaries

Caddy serves only frontend assets and license files and proxies only `/api/*`,
`/health`, and `/ready`. Native inference and PostgreSQL stay on loopback. The
legacy transient `/api/chat` is disabled in the public profile to prevent bypassing
session ownership and quotas. Public OpenAPI/docs are disabled.

Public API writes require an exact configured Origin, JSON content type when a
body is present, and reject cross-site Fetch Metadata. Host must match the public
origin. No permissive CORS is added. Cookies remain HttpOnly, SameSite=Lax, `/api`,
fixed 30-day expiry, with Secure=true publicly. No account system is added.
Application request bodies are capped at 64 KiB including chunked bodies before
JSON parsing. Safe errors and security headers do not echo prompts or credentials.

The backend trusts only Caddy's overwritten `X-Real-IP` from its loopback peer;
Uvicorn proxy-header rewriting is disabled. Client-supplied forwarding headers
must not bypass IP limits. No CDN/proxy in front of Caddy is assumed.

## Resource controls

- Public API requests: per-IP 60/minute; authenticated inference: per-session
  6/minute. Fixed windows, bounded process-local tracking; overload returns 429
  with Retry-After. Session creation is also covered by IP limits.
- Daily UTC quotas: 50 admitted requests and 32768 reserved output tokens per
  session; 200 requests and 131072 output tokens per IP. Durable PostgreSQL quota
  rows atomically reserve the requested max_tokens budget with the turn before
  queue admission and streaming. A failed/cancelled reserved turn remains charged, including a reservation later
  rejected by a full/expired queue; there are no automatic refunds
  based on missing native usage. This conservative policy bounds generation work.
- One active inference and two FIFO waiters. Queue wait defaults to 10 seconds;
  full/expired queues return 503 with Retry-After, no upstream request. Queued
  cancellation releases its slot and records cancelled state. Permits survive
  until upstream closure and durable finalization. Shutdown/cancellation cannot
  leak permits. No Redis, distributed queue, or multiple workers.
- Existing max output 2048/default1024, prompt bounds and generation deadlines
  remain. Requests rejected before admission must not invoke native inference.
- systemd examples provide memory/CPU/task limits and restart policies. They are
  prepared files, not installed/running services. Native model loads once in its
  own process; public backend never starts it.

`/health` is cheap liveness; `/ready` checks the required quota table/SELECT
privilege and native health with bounded I/O and safe 503 on dependency failure. JSON application logs record a
server-generated request ID, method, route template/status, duration, and stream
completion, without raw paths/query text, IPs, cookies, bodies, prompts or DSNs.

## Tests and deferred checks

Independent tests derive expectations from this contract and PROJECT_SPEC Phase4.
Exercise rate/quota boundaries, shared-IP/new-session behavior, queue fairness,
saturation, timeout/disconnect cleanup, CSRF/Host/body/forwarded-IP checks,
readiness and safe logs. Database tests use isolated `_test` schemas. Public DNS,
ACME issuance, public HTTPS/firewall, installed service restart and production
restore remain deployment-time checks. Do not describe them as executed yet.

## Deployment status and ownership

All settings above are implemented application controls, not evidence of public
deployment. Native and database bindings, systemd limits, TLS, firewall rules,
least-privilege roles, abuse contact, and backup scheduling must be validated by
the operator using [09](09-deployment-guide.md) and [11](11-operations-and-troubleshooting.md).
No public configuration is active in this checkout. The opt-in profile prevents
the local development command from silently becoming a public deployment.

Quota subjects use SHA-256 hashes of canonical IP addresses and session IDs. IP
hashes are pseudonyms, not anonymization; keep database access private. Quota rows
older than the retention cutoff are removed only by the explicit maintenance
apply command. Minute counters have at most 4096 combined IP/session entries;
a full tracking map returns503 instead of growing without limit. Uvicorn also
bounds concurrent connections/tasks at64. Shared NAT users share IP budgets.
Quotas limit work; they do not implement a semantic prohibited-content filter.

## Why these are separate controls

If four people press Send together, one generates, two wait, and the fourth gets
a503. That queue bounds simultaneous work. A minute limit stops one browser or
IP from repeatedly claiming those places. The durable daily budget bounds total
work even across backend restarts or newly created browser sessions. Requesting
1024 output tokens reserves1024 even if the answer uses2: this sacrifices exact
billing fairness to prevent disconnects or missing usage from refunding costly
work. Actual measured usage is still saved separately for later analysis.
