# 09 — Prepare now, deploy manually later

**No public deployment has been performed.** Tommy asked for Phase 4 preparation
and deployment instructions, explicitly postponing deployment. The files in
`deploy/` are templates. No production systemd units, public listeners, DNS,
firewall rules, ACME certificates, or Caddy installation were changed. Phase 4's
public-access completion criterion remains pending until the later go-live checks
below pass. Phase5 local experiments are authorized as the final implementation
milestone; Dockerization is removed, and public deployment remains deferred.

The intended topology is:

```text
browser -- HTTPS :443 --> Caddy (static allowlist + API reverse proxy)
                            |
                      127.0.0.1:8000 (one backend worker)
                         /         \
              127.0.0.1:5432    127.0.0.1:8081
              PostgreSQL 18     pinned CPU Llama 3.2 1B Instruct
```

Caddy is the only public entry. A loopback address is a machine-local boundary,
not authentication between local accounts: use a trusted single-tenant VPS.
Session cookies grant access to stored chats; anyone with a copied cookie can
access that session. This portfolio demo has anonymous sessions, not accounts.

## Before the future deployment

1. Supply a domain and an ACME email address. Point its A record to this VPS;
   publish AAAA only if IPv6 is correctly routed and firewalled. Do not put a
   CDN or another proxy in front: the IP trust contract assumes direct Caddy
   connections. Review [automatic HTTPS prerequisites](https://caddyserver.com/docs/automatic-https).
2. Inventory services, ports, disk, and memory: `ss -lntup`, `free -h`, `df -h`,
   `systemctl status caddy postgresql`. Preserve existing services and SSH access.
   If Caddy or another server already owns 80/443, merge the reviewed site into
   its existing configuration; do not overwrite or stop it using these examples.
3. Review [the security contract](10-security-and-resource-management.md), the
   complete [model license](../legal/LLAMA-3.2-LICENSE.txt) and
   [Acceptable Use Policy](../legal/LLAMA-3.2-USE-POLICY.md). Tommy accepted the
   model terms on 2026-11-08. Preserve that attestation and the pinned artifact
   record; no fresh acceptance or model download is needed for the same bytes.
   Keep **Built with Llama** prominent, retain the downloadable agreement and
   NOTICE, and add an actual monitored abuse contact to the site before launch.
   The owner must handle prohibited use; quotas are not a content filter.
4. Run the documented ordinary, database, and live inference tests in the
   activated `llama` environment. Database tests require a dedicated `_test`
   database and isolated schemas. Never point tests at the application database.
5. Commit the reviewed Phase 4 changes and record the deployment commit. Take a
   checked backup using [the operations guide](11-operations-and-troubleshooting.md)
   before schema changes. Review migration SQL, private configuration, and the
   proposed firewall diff before executing anything.

## Stage a separate, readable application copy

The checkout is currently under `/root`, which dedicated service users cannot
traverse. Do not loosen `/root` permissions or recursively change this checkout's
owner. The following are **future operator commands**, not an installation script.
They assume these new paths/accounts do not already exist; inspect conflicts
first. Run from the reviewed checkout. `DEPLOY_COMMIT` must include these files.

```bash
DEPLOY_COMMIT=REPLACE_WITH_REVIEWED_COMMIT
sudo useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin llamaapp
sudo useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin llamainfer
sudo install -d -o root -g root -m 0755 /opt/llama /opt/llama/app /opt/llama/app/.runtime
git archive "$DEPLOY_COMMIT" | sudo tar -x -C /opt/llama/app
sudo chown -R root:root /opt/llama/app
sudo chmod -R a+rX /opt/llama/app
```

Prepare a **new** private Conda prefix at `/opt/llama/env` from
`environment.yml`, using the existing Conda installation as an administrator.
Do not point a system service at the Python interpreter inside `/root`.
On a host without the default-channel terms plugin issue, the ordinary command
is:

```bash
sudo "$CONDA_EXE" env create --prefix /opt/llama/env --file /opt/llama/app/environment.yml
sudo chmod -R a+rX /opt/llama/env
```

`CONDA_EXE` is the absolute executable exported by Conda activation. Check that
it is set before running this command; a shell function named `conda` cannot be
executed by systemd or sudo directly.

On this VPS the Conda terms plugin checks globally configured default channels
before processing an environment file. Use the same explicit per-command
conda-forge override as the tested project setup, with a new prefix:

```bash
sudo "$CONDA_EXE" create --override-channels -c conda-forge --prefix /opt/llama/env \
  python=3.11 httpx=0.28.1 pytest=9.1.1 ruff=0.17.1 anyio=4.16.0 \
  fastapi-core=0.162.2 uvicorn=0.54.0 pydantic=2.14.3 nodejs=24.22.1 \
  sqlalchemy=2.0.54 alembic=1.20.1 psycopg=3.3.8
sudo chmod -R a+rX /opt/llama/env
```

Choose one creation method, not both. `environment.yml` remains the dependency
source of truth: compare these fallback pins with it before running, especially
after an update. Review Conda's proposed package transaction. This does not
accept other channel terms or change global channel settings; creating the new
production prefix remains unexecuted deployment work.

Copy the existing pinned runtime source **including its `.git` directory and
native `build/bin` shared libraries**, plus the already verified model. Do not
rebuild, switch models, or copy all of `.runtime` (it contains credentials).
Copy the existing acceptance/verification evidence to a private operator backup
too. Native startup verifies the runtime source commit, executable revision,
model checksum, and GGUF metadata before serving. The unit grants Git trust only
to this root-owned copied runtime path; it does not make source or weights writable.

```bash
sudo cp -a .runtime/llama.cpp /opt/llama/app/.runtime/llama.cpp
sudo chown -R root:root /opt/llama/app/.runtime/llama.cpp
sudo chmod -R a+rX /opt/llama/app/.runtime/llama.cpp
sudo install -d -o root -g llamainfer -m 0750 /opt/llama/assets
sudo install -o root -g llamainfer -m 0640 \
  .runtime/models/Llama-3.2-1B-Instruct-Q4_K_M.gguf \
  /opt/llama/assets/Llama-3.2-1B-Instruct-Q4_K_M.gguf
sudo install -d -o root -g root -m 0755 /etc/llama
sudo install -o root -g llamainfer -m 0640 deploy/inference.env.example /etc/llama/inference.env
sudo install -o root -g llamaapp -m 0640 deploy/backend.env.example /etc/llama/backend.env
sudo install -o root -g root -m 0644 deploy/caddy.env.example /etc/llama/caddy.env
```

Edit the new environment files privately. EnvironmentFile syntax has no `export`
and no shell expansion. Replace all `YOUR_DOMAIN`, `YOUR_ACME_EMAIL`, and password
placeholders. `LLAMA_PUBLIC_ORIGIN` is exactly `https://YOUR_DOMAIN`, with no
path or trailing slash; `LLAMA_DOMAIN` is the same hostname alone. Keep
`LLAMA_SESSION_COOKIE_SECURE=true`, the native upstream on loopback, and one
backend worker. Never print the real backend environment or commit passwords.
URL-encode password characters in SQLAlchemy URLs; `.pgpass` uses libpq escaping
instead. The nonsecret Caddy environment must be available to both its service
and manual validation/reload commands.

## Use separate runtime and migration database roles

Keep the existing PostgreSQL 18 cluster and database. Verify
`SHOW listen_addresses` returns loopback and inspect `pg_hba.conf`: require SCRAM
for dedicated loopback roles; do not add public PostgreSQL rules. The Phase 3
`llama_app` role owns the application schema and becomes the migration-only role.
Its credentials must not be in the backend service file.

For the future deployment, create a fresh runtime login with an interactive
password prompt. In `sudo -u postgres psql -d llama`, review and execute:

```sql
CREATE ROLE llama_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
\password llama_runtime
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE llama TO llama_runtime;
GRANT USAGE ON SCHEMA public TO llama_runtime;
```

Stop the backend before migrations so startup recovery and schema changes have a
single owner. In the activated operator environment, privately export the
**migration** `LLAMA_DATABASE_URL` for `llama_app`, then run:

```bash
alembic upgrade head
alembic check
unset LLAMA_DATABASE_URL
```

Apply the explicit runtime grants listed in
`deploy/runtime-grants.sql` as the schema owner or database administrator.
Reapply/review these grants after each migration that adds application tables.
They grant DML on application tables, not DDL or access to `alembic_version`.
The backend uses `llama_runtime` in `/etc/llama/backend.env`. It must be unable to
create/drop tables or assume the migration role. Do not use a superuser or the
test role in the application. PostgreSQL roles and credentials are separate from
OS service users. See [PostgreSQL privileges](https://www.postgresql.org/docs/18/ddl-priv.html).

## Review service limits and validate before installation

The native unit starts `python -m inference.server`, which replaces Python with
the verified native server. The backend starts `python -m backend --port 8000`
and never launches a model process. `KillMode=control-group` stops remaining
children; systemd enforces the final stop deadline. `After=` controls ordering,
not readiness: wait for `/ready` before enabling public traffic.

The proposed ceilings are native 3 GiB/300% CPU, backend 512 MiB/100%, Caddy
256 MiB/100%, with task/file limits. `300%` means three CPU cores' time, not 300%
of the whole machine. These are starting ceilings for the recorded 16 GiB VPS,
not new performance measurements. Four inference threads share that three-core
budget. Leave headroom for PostgreSQL, the OS, and existing services; lower CPU
and task limits if necessary and rerun live checks. A too-low memory limit can
kill an otherwise correct model load. No global PostgreSQL/system tuning is
performed by these files. [systemd resource controls](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html)
and [stop semantics](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html)
explain these settings.

For a fresh Caddy installation only, prevent package post-install startup until
configuration is ready. Inspect existing units first; do not mask an existing
web service. Follow the [official install documentation](https://caddyserver.com/docs/install)
for your distribution and record the chosen package version.

```bash
# Future go-live only; these commands have NOT been run.
sudo systemctl mask caddy.service
sudo apt-get install --no-install-recommends caddy
sudo install -d -m 0755 /etc/systemd/system/caddy.service.d
sudo install -m 0644 deploy/systemd/caddy-llama.conf /etc/systemd/system/caddy.service.d/llama.conf
sudo install -o root -g caddy -m 0640 deploy/Caddyfile /etc/caddy/Caddyfile
sudo install -m 0644 deploy/systemd/llama-inference.service /etc/systemd/system/llama-inference.service
sudo install -m 0644 deploy/systemd/llama-backend.service /etc/systemd/system/llama-backend.service
```

The official package supplies the nonroot Caddy user, certificate storage, and
privileged-port capabilities. Keep those defaults. The Caddyfile serves only the
four frontend files and three legal files; it never serves the repository root.
It overwrites `X-Real-IP` and removes inbound forwarding headers. Do not enable
Uvicorn proxy-header rewriting or add another trusted proxy without redesigning
and testing this boundary.

Caddy automatically flushes `text/event-stream`. Keep the default
`flush_interval`: a negative value also disables backend cancellation when the
browser disconnects. Neither response buffering nor compression is configured.
The request upload deadline is 10 seconds; API bodies are limited to 65,536
bytes. Caddy's 150-second response deadline leaves room for the application's
120-second generation budget and queue/cleanup time. If you change application
deadlines, review proxy deadlines too. [Caddy streaming behavior](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
and [body limits](https://caddyserver.com/docs/caddyfile/directives/request_body)
describe these controls. Caddy access logging is not enabled; application logs
are deliberately redacted. Avoid debug logging or unrestricted access logs.

```bash
sudo systemd-analyze verify /etc/systemd/system/llama-inference.service /etc/systemd/system/llama-backend.service
sudo sh -c 'set -a; . /etc/llama/caddy.env; set +a; caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile'
sudo -u llamaapp test -x /opt/llama/env/bin/python
sudo -u llamainfer test -r /opt/llama/assets/Llama-3.2-1B-Instruct-Q4_K_M.gguf
sudo -u caddy test -r /opt/llama/app/frontend/index.html
sudo systemctl daemon-reload
sudo systemctl start llama-inference.service llama-backend.service
curl --fail --max-time 5 http://127.0.0.1:8081/health
curl --fail --max-time 10 -H 'Host: YOUR_DOMAIN' http://127.0.0.1:8000/ready
```

If staging fails, stop only the newly created units; leave PostgreSQL and other
services intact. Do not proceed to public traffic while readiness fails.

## Future go-live checklist

This section is intentionally manual. Run it only when Tommy chooses to deploy
and supplies the real domain.

1. Confirm tested backups and a rollback release/configuration exist. Record
   the commit, model hash, environment package versions, service limits, and
   deployment date. Review the live license/AUP and add the abuse contact.
2. Verify DNS from outside the VPS. Review the current host/provider firewall;
   keep the current SSH rule and an active second SSH session. Allow inbound
   TCP 80/443 to Caddy, plus the existing SSH management path. Do not reset the
   firewall or open 5432/8000/8081/2019. This template does not require UDP443.
3. On a fresh Caddy installation, `sudo systemctl unmask caddy.service`, then
   `sudo systemctl start caddy.service`. Inspect its journal for successful ACME
   issuance. Test HTTPS before enabling boot startup. HTTP should redirect to
   HTTPS; never bypass certificate validation using `curl -k`.
4. From another machine, verify the commands below and open the page in a
   browser. Confirm the secure cookie is HttpOnly, SameSite=Lax, path `/api`,
   with Secure. Generate a short response, verify progressive chunks, press
   Stop, and immediately send another turn. Reload and confirm persisted chat.
   Check that forged Origin/forwarding headers cannot bypass limits, oversized
   bodies return 413, saturation returns 503, and quotas/rate limits return 429
   with Retry-After. Use isolated test sessions/data for destructive checks.
5. Inspect `ss -lntup`: backend 8000, native 8081, PostgreSQL 5432, admin 2019 must
   bind loopback only. Independently confirm these ports are unreachable from
   outside. Verify both IPv4 and any published IPv6 route.
6. Perform the controlled restart/recovery and fresh-database restore drill in
   the operations guide. Only after the checks pass, enable the new services:
   `sudo systemctl enable llama-inference.service llama-backend.service caddy.service`.
   Record the real results; preparation alone does not satisfy public HTTPS or
   installed-service recovery validation.

```bash
curl --fail --max-time 15 -I http://YOUR_DOMAIN/
curl --fail --max-time 15 -I https://YOUR_DOMAIN/
curl --fail --max-time 15 https://YOUR_DOMAIN/health
curl --fail --max-time 15 https://YOUR_DOMAIN/ready
curl --fail --max-time 15 https://YOUR_DOMAIN/legal/LLAMA-3.2-LICENSE.txt
curl --max-time 15 -i https://YOUR_DOMAIN/.env
curl --max-time 15 -i https://YOUR_DOMAIN/openapi.json
curl --max-time 15 -i https://YOUR_DOMAIN/api/chat
```

Expect 200 from health/ready and legal files after dependencies are ready;
unknown/private/docs/legacy paths must be 404. Confirm HSTS, CSP, nosniff,
frame denial, referrer policy, and permissions policy on HTTPS responses.
HSTS is host-only; the template does not preload or cover unrelated subdomains.


## Phase5 findings and deployment ceilings

[The performance report](16-performance-findings.md) records unthrottled local
CPU experiments. Its fastest native profiles are not automatically deployment
defaults. These systemd examples still cap inference at300% CPU, while several
benchmarks used about400% and experimental six-thread runs can use more. Recheck
performance under the installed ceilings before promising a public capacity/SLO.
Active Q4, f16 KV, four threads, one native slot and one active backend generation
remain unchanged. Prefix caching remains disabled for anonymous application turns.
There is no Docker/Compose deployment path in the current final scope.
