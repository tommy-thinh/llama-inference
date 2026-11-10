# 11 — Operate and recover the prepared deployment

These are **future operator procedures**. The repository preparation did not
install/start systemd services, expose public ports, issue certificates, change
firewalls, or restore an application database. Use the actual domain in place of
`YOUR_DOMAIN`. See [deployment staging and go-live](09-deployment-guide.md).

## Understand the failure boundary

`/health` asks whether the backend is alive. `/ready` checks whether PostgreSQL
and the separate native inference process can respond within bounded time.
For example, a running backend with a stopped model process should remain alive
but report not ready. Restart the failed component; do not hide the failure by
changing the model or launching another backend worker.

One backend process owns its in-memory admission queue and short-window rate
limits. PostgreSQL owns durable chats and daily quota reservations. Restarting
the backend resets short-window counters, not daily quota rows. An admitted
failed/cancelled turn remains charged conservatively. A short-window 429 and a
daily-quota 429 both mean wait; a full/expired queue returns 503 with Retry-After.
Existing sessions do not provide a way around per-IP quotas.

## Diagnose before restarting

```bash
sudo systemctl status llama-inference.service llama-backend.service caddy.service
curl --fail --max-time 5 http://127.0.0.1:8081/health
curl --fail --max-time 10 -H 'Host: YOUR_DOMAIN' http://127.0.0.1:8000/ready
curl --fail --max-time 15 https://YOUR_DOMAIN/ready
sudo journalctl -u llama-backend.service --since '15 minutes ago' --no-pager
sudo journalctl -u llama-inference.service --since '15 minutes ago' --no-pager
sudo journalctl -u caddy.service --since '15 minutes ago' --no-pager
sudo systemctl show llama-inference.service llama-backend.service -p MemoryCurrent -p MemoryPeak -p CPUUsageNSec -p NRestarts
ss -lntup
df -h
```

Keep journals private. Application JSON logs contain generated request IDs,
route templates, method, status, duration, and stream completion, without
prompts, bodies, cookies, IPs, query text, or database URLs. Match a reported
request ID to the log event; do not ask users to post cookies or chat content.
Do not enable native verbose-prompt logging, Caddy debug logging, unrestricted
access logging, or Uvicorn raw-path access logs. Keep journal storage bounded
using the host's existing journald retention policy; changing that shared policy
needs its own review. No log retention system was silently installed.

| Symptom | Likely boundary and next check |
| --- | --- |
| Caddy 502 | Backend unavailable: inspect service status and loopback port 8000; do not expose that port publicly. |
| `/health` 200, `/ready` 503 | Database/model unavailable or slow; inspect each dependency's local health and journals. |
| Native fails before listening | Missing copied shared libraries/source `.git`, wrong pinned revision/hash, unreadable model, or Git safe.directory mismatch. Preserve verification failures. |
| Public write 403 | Wrong exact Origin/Host, cross-site Fetch Metadata, or unsupported content type; use the same HTTPS origin. |
| Session repeatedly resets | Inspect Secure/HttpOnly/SameSite/path in browser storage; HTTP cannot use the public Secure cookie. |
| 413 | Body exceeds 64 KiB; reduce payload/history. Changing Caddy alone does not change the application cap. |
| 429 | Respect Retry-After; sessions on shared NAT share IP limits. Daily reservations reset at UTC day boundaries. |
| 503 with Retry-After | One generation plus two waiters is saturated, or queue wait expired. Do not add workers to bypass it. |
| SSE appears only at the end | Confirm text/event-stream, no response buffering/compression, no intermediate CDN. Keep default flush_interval. |
| Stop leaves generation busy | Verify browser disconnect reaches Caddy/backend; negative flush_interval must not be set. Check cancellation/finalization events. |
| `Result=oom-kill` or repeated restart | Inspect memory peak and other workloads before adjusting ceilings. Never silently substitute a smaller model. |
| ACME failure | Check A/AAAA, public80/443, certificate-storage permissions, provider firewall, and conflicting web servers. |

The fixed queue bounds waiting work; rate limits reduce repeated admissions;
systemd memory/CPU ceilings bound each process if an application check fails.
They do not protect against volumetric network attacks or unlimited disk growth.
Monitor disk space, journal size, database size, failed requests, queue rejection,
memory, and backup age. Stop public access if these cannot be controlled.

## Controlled restart and recovery drill

During a maintenance window, keep one browser session with a saved conversation
and generate a short turn. Restart only the backend, then reload and confirm the
same committed messages remain. A request interrupted by restart should be
recovered as failed with its latest durable partial text; it must not remain
running forever. Daily quota reservations remain charged.

```bash
sudo systemctl restart llama-backend.service
curl --fail --max-time 10 -H 'Host: YOUR_DOMAIN' http://127.0.0.1:8000/ready
```

Then restart native inference and check both native health and backend readiness.
The backend may temporarily return safe failure while the model reloads.

```bash
sudo systemctl restart llama-inference.service
curl --fail --max-time 5 http://127.0.0.1:8081/health
curl --fail --max-time 10 -H 'Host: YOUR_DOMAIN' http://127.0.0.1:8000/ready
```

An immediate curl can fail while startup is in progress. Retry within a bounded
120-second operator window, then inspect logs instead of waiting indefinitely.
Units use restart-on-failure with a five-start/300-second cap. After fixing the
cause of a start-limit failure, use `sudo systemctl reset-failed UNIT`, then
start that unit. `systemctl stop` is intentional and does not auto-restart.
`KillMode=control-group` and `TimeoutStopSec=20` stop remaining child processes.
After a controlled stop, check that the former unit cgroup has no tasks and its
owned listener is gone; never kill a process merely because it occupies a port.

Caddy reloads keep the existing configuration on validation failure. Supply
its nonsecret environment when manually validating, and use the package reload
command so the service's EnvironmentFile is applied:

```bash
sudo sh -c 'set -a; . /etc/llama/caddy.env; set +a; caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile'
sudo systemctl reload caddy.service
```

## Create private logical backups

This demo starts with manual PostgreSQL18 logical backups. PostgreSQL `pg_dump`
creates a consistent snapshot of one database without requiring a backend stop;
roles are not included. The custom archive format supports `pg_restore`.
[PostgreSQL pg_dump](https://www.postgresql.org/docs/18/app-pgdump.html) explains
the formats and limits. This is not point-in-time recovery: the maximum possible
data loss is the time since the last good snapshot. Set a reviewed schedule and
off-VPS retention before real use (for example daily snapshots plus one before
updates); no automatic timer, pruning, or cloud upload was installed.

Create a distinct read-only database role, not a superuser or the runtime writer.
As PostgreSQL administrator in database `llama`:

```sql
CREATE ROLE llama_backup LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
\password llama_backup
GRANT CONNECT ON DATABASE llama TO llama_backup;
GRANT USAGE ON SCHEMA public TO llama_backup;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO llama_backup;
```

Reapply the read-only grant after migrations add tables. No sequences currently
exist: IDs are application-generated UUIDs. If future migrations add sequences,
review their backup privileges separately.

Create a backup OS account/home and private destination as future operator work:

```bash
sudo useradd --system --user-group --create-home --home-dir /var/lib/llamabackup --shell /usr/sbin/nologin llamabackup
sudo chmod 0700 /var/lib/llamabackup
sudo install -d -o llamabackup -g llamabackup -m 0700 /var/backups/llama
sudo install -o llamabackup -g llamabackup -m 0600 /dev/null /var/lib/llamabackup/.pgpass
sudoedit /var/lib/llamabackup/.pgpass
```

The one `.pgpass` line is `127.0.0.1:5432:llama:llama_backup:PASSWORD`, with actual
password kept private. Escape colon/backslash in libpq password files; never
put a password in the command line. See
[PostgreSQL password files](https://www.postgresql.org/docs/18/libpq-pgpass.html).
Do not reuse `/etc/llama/backend.env` as a shell script. Check free disk space
before dumping and use PostgreSQL18 client tools in PATH.

```bash
sudo -u llamabackup env \
  PGHOST=127.0.0.1 PGPORT=5432 PGDATABASE=llama PGUSER=llama_backup \
  PGPASSFILE=/var/lib/llamabackup/.pgpass \
  timeout 5m /opt/llama/app/deploy/backup-database.sh /var/backups/llama
```

The helper requires loopback/simple database names, a current-user 0600 password
file, and a current-user 0700 destination. It writes a 0600 temporary custom-format
archive in the same directory, checks command success/nonempty output/archive
table-of-contents, and atomically links the complete file to a unique `.dump`
name without overwriting anything. Failure before publication removes its
temporary file; SIGKILL/power loss can leave a hidden `.part` file, which is
never a published backup. Listing an archive does not prove its rows restore:
perform the drill below. The helper does not fsync storage or guarantee
power-loss durability.

After success, compute and record a SHA-256 alongside deployment metadata in
private storage. Copy the checked archive, checksum, migration revision,
operator role-recreation instructions, and private configuration backup to
encrypted off-VPS storage; verify the copy's checksum. Restrict access because
archives contain chats, session hashes, and request metadata. Do not serve them
through Caddy or commit them. Keep model weights separate and retain their
original verification record. Deleting older snapshots is a separate reviewed
operation, after checking off-site copies and the restore drill.

## Restore only into a new database

Never restore over the live application database. Only restore a trusted archive
with matching recorded checksum; PostgreSQL archives can execute SQL. First
rehearse in a **new dedicated database ending `_test`**, without application data.
For a real recovery, use another fresh database such as `llama_recovered_YYYYMMDD`,
keep the original intact, and switch configuration only after validation.

1. Record the archive path, checksum, source PostgreSQL version, migration
   revision, and target name. Stop public writes for the final recovery cutover,
   not for an isolated rehearsal. Have an administrator create the empty target,
   owned by the migration role. Never use `--clean` or `--create` against the
   application database.
2. As the trusted restore operator, use a private0600 `.pgpass` for `llama_app`
   and the new target. Set the PG variables explicitly; do not export a DSN to
   your shell history.

```bash
# Rehearsal example; pick a fresh name that does not already exist.
sudo -u postgres createdb --owner=llama_app llama_restore_YYYYMMDD_test
export PGHOST=127.0.0.1 PGPORT=5432 PGDATABASE=llama_restore_YYYYMMDD_test PGUSER=llama_app
export PGPASSFILE=/absolute/private/path/to/restore.pgpass
ARCHIVE=/absolute/private/path/to/checked-backup.dump
pg_restore --list "$ARCHIVE" > /dev/null
pg_restore --no-password --exit-on-error --single-transaction --no-owner --no-acl \
  --dbname="$PGDATABASE" "$ARCHIVE"
psql --no-password --set=ON_ERROR_STOP=1 --command='SELECT version_num FROM alembic_version'
psql --no-password --set=ON_ERROR_STOP=1 --command='SELECT count(*) FROM conversations'
psql --no-password --set=ON_ERROR_STOP=1 --command='SELECT count(*) FROM messages'
unset PGHOST PGPORT PGDATABASE PGUSER PGPASSFILE ARCHIVE
```

3. Compare counts with the snapshot record. Check foreign keys and terminal
   request states with the matching application release. Do not run app tests
   against a restored copy of real users' data. For a synthetic `_test` drill,
   test conversation retrieval/ownership after restore using only its synthetic
   sessions. Old anonymous sessions expire; row counts alone are not an ownership
   test. Document duration and outcome, then remove only the known rehearsal DB
   when finished.
4. For a real cutover, reapply runtime grants in the restored database (edit the
   `GRANT CONNECT ON DATABASE` target in `deploy/runtime-grants.sql`), run reviewed
   Alembic migrations if required, and privately change only the database name
   in backend configuration. Restart the single backend and check readiness,
   retrieval, new generation, and cancellation. Keep the original database for
   rollback until the new copy is confirmed. Daily quotas restore to snapshot
   time, so recent quota reservations and writes after the backup may be lost.

[PostgreSQL pg_restore](https://www.postgresql.org/docs/18/app-pgrestore.html)
documents transaction/error behavior. A logical restore drill does not test
machine replacement, encrypted off-site retrieval, DNS, or certificate recovery;
include those separately in a future full-host disaster recovery exercise.

## Update and roll back deliberately

Take a checked backup first. Review the release diff, migration SQL, environment
changes, runtime/model pins, and privilege grants. Stop public writes and the
single backend during the update. Stage the reviewed app and environment in new
paths, retaining the previous release and configuration; never run two backend
workers concurrently against the same database. Review/update the unit paths
and the native unit's scoped Git safe.directory when switching release paths.
Apply Alembic with the migration role, reapply grants, then restart native only
if its executable/model configuration changed, and restart the backend. Validate
readiness and browser streaming/cancellation before restoring public access.

Do not modify the original pinned model or rebuild the runtime as routine
deployment maintenance. Any byte/pin change requires fresh checksum, GGUF
metadata, runtime revision, and real-inference validation. Preserve the existing
license attestation and historical Qwen results with their labels.

For code/config-only failure with compatible schema, stop the new backend,
restore the prior reviewed unit/config paths, daemon-reload if needed, and start
that single previous version. If schema compatibility is uncertain, keep the
demo unavailable and restore the pre-update snapshot into a fresh database with
the matching previous release. Never guess an Alembic downgrade or overwrite
the original database. Recheck HTTPS, readiness, ownership, generation, and Stop.

Retention remains explicit: `python -m backend.retention --days 30` previews in
the activated operator environment using private runtime credentials;
`--apply` deletes eligible rows. Back up first, read the preview, and use
`--apply` only when that deletion is intended. This phase does not schedule
automatic chat deletion or backup pruning.

## Evidence required before claiming deployed

Record the actual domain/certificate validation, external port checks,
loopback-only dependency listeners, service users/limits, real SSE cancellation,
restart persistence, failed-dependency readiness, checked backup/off-site copy,
and fresh-database restore outcome. The repository's mocked/loopback tests can
validate templates and boundaries, but cannot establish that DNS, ACME,
firewalls, installed services, or production recovery work. These checks remain
deferred until Tommy chooses to deploy.
