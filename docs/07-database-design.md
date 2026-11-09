# 07 — PostgreSQL database design

Phase 3 is authorized on 2026-11-09. PostgreSQL 18 stores anonymous sessions,
conversations, messages, and inference requests. SQLAlchemy 2 maps those rows to
Python objects; Alembic versions the schema. Inference still runs in llama.cpp.

```mermaid
erDiagram
    sessions ||--o{ conversations : owns
    conversations ||--o{ inference_requests : records
    conversations ||--o{ messages : contains
    inference_requests ||--o{ messages : produces
```

Each table has a UUID primary key: a stable identifier for one row. Foreign keys
make ownership and request/message relationships explicit. Deleting an eligible
conversation cascades to its requests/messages, and deleting an eligible session
cascades to its conversations. Application queries always also check the owner;
an unguessable UUID is not authorization.

## Model contract

- `sessions`: id, unique SHA-256 token hash, created_at, last_seen_at, expires_at.
  The raw random cookie is never stored in the database.
- `conversations`: id, session_id, title (1–80 nonblank characters), created_at,
  updated_at, next_position. Unique positions order messages independently of
  timestamp precision.
- `inference_requests`: id, conversation_id, status (`running`, `completed`,
  `cancelled`, `failed`), generation settings, model alias, started_at, finished_at,
  TTFT/latency, prompt/completion token counts, decode speed, safe error code.
- `messages`: id, conversation_id, request_id, position, role (`user`, `assistant`),
  content, status (`partial`, `completed`, `cancelled`, `failed`), created_at,
  updated_at. Each request owns one user and one assistant message.
- Unique conversation/message position and request/role constraints prevent
  duplicate turns. A partial unique index permits one running request per
  conversation. Status/role/metric checks reject invalid values at the database.
- Indexes cover token hash, session expiry, owner conversations ordered by update,
  ordered conversation messages, and conversation request history.

Creating the request and its two messages is one transaction under a conversation
row lock. It commits before upstream generation begins; no connection or row lock
is held for the duration of a stream. Concurrent attempts for the same conversation
receive 409. A failed transaction leaves no dangling request or message.

Assistant output checkpoints are stored at most every 250 ms during streaming and
fully flushed at terminal state. Normal completion is committed before `[DONE]`
is sent. Timeout/upstream errors record failed state; disconnect records cancelled
state. Partial text survives both. A database outage cannot be represented as a
successful durable completion: emit a safe persistence error and preserve the last
committed checkpoint. Finalization is cancellation-shielded but bounded to five
seconds. Restart recovery marks leftover running requests failed while preserving
their content. This assumes the existing single backend worker; multiple workers
need a lease/recovery design before use.

## Migrations, credentials, and retention

Use `alembic upgrade head` explicitly; application startup does not create tables.
`LLAMA_DATABASE_URL` is a PostgreSQL psycopg URL supplied through the environment.
An independent test role/database uses `LLAMA_TEST_DATABASE_URL`. Database tests
require `pytest --run-database`; the test database name must end in `_test` and
tests isolate their own temporary schemas. Never use application data for tests.

The application role owns only its dedicated database and has no superuser,
role-creation, or database-creation privileges. PostgreSQL listens only on loopback
and uses SCRAM password authentication. Generated credentials live in an ignored,
mode-0600 environment file, never in committed source or command-line output.

Basic retention is an explicit maintenance command, defaulting to a 30-day cutoff.
Dry run is the default; `--apply` is required to delete. It removes expired sessions
and old conversations only when they have no running request. It never runs an
automatic destructive job at application startup.

References: [SQLAlchemy async sessions](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html),
[Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html),
[PostgreSQL SCRAM authentication](https://www.postgresql.org/docs/18/auth-password.html).

## Why keys and transactions matter

A primary key answers “which exact row?” For example, a conversation UUID stays
stable even when its title changes. A foreign key answers “does its parent exist?”
A message cannot refer to a deleted request. Its `(conversation_id, position)`
unique key prevents two messages occupying the same slot. Indexes let PostgreSQL
find one owner's recent conversations without scanning everyone's history.
Indexes use RAM/disk and add write work, so this schema indexes actual queries.

A transaction is an all-or-nothing unit. Reserving a turn must create a request,
its user message, its empty assistant message, and advance the next position
together. If inserting either message fails, PostgreSQL rolls everything back.
The conversation lock serializes two submissions to the same conversation; the
partial unique index is an additional database guarantee. See PostgreSQL's
[row-lock documentation](https://www.postgresql.org/docs/18/explicit-locking.html).

Streaming is deliberately outside that transaction: holding a connection and row
lock while a model generates for a minute would exhaust a small connection pool.
Short checkpoint transactions persist visible text. Completing the request and
assistant message is another atomic transaction. A completed user message may
belong to a failed request; prompt reconstruction filters by the request's state,
so neither half of an unsuccessful turn becomes model context.

The pool has five connections, zero overflow, a five-second checkout timeout,
and five-second connection/statement limits. SQLAlchemy sessions are local to
one operation, never shared between concurrent tasks. A generation deadline also
bounds checkpoint waits. All terminal writes and the response disconnect fallback
share one cancellation-shielded five-second cleanup budget. An unavailable DB
can leave a running checkpoint; the next single-worker startup marks it failed.
This preserves evidence instead of inventing a successful completion.

## Local setup

On this VPS PostgreSQL **18.6** is installed as cluster `18/main`, database `llama`
with role `llama_app`, and separate database/role `llama_test`. Generated passwords
are in `.runtime/database.env` (mode 0600). This file is ignored by Git. Do not
print it, paste it into issues, or replace it with example passwords.

From the repo root, with `conda activate llama`:

```bash
source .runtime/database.env
alembic upgrade head
alembic current
python -m backend
```

`source` exports the URLs for that terminal. `.env` files are not auto-loaded.
`python -m inference.server` runs separately; migrations never rebuild it or touch
weights. SQLAlchemy, Alembic, and psycopg pins live in `environment.yml`.

For a **fresh Ubuntu 26.04 machine**, inspect existing PostgreSQL services before
installing; preserve existing clusters. The following is for a new cluster only:

```bash
sudo apt-get install --no-install-recommends postgresql-18
sudo -u postgres createuser --pwprompt --no-superuser --no-createdb --no-createrole llama_app
sudo -u postgres createdb --owner=llama_app llama
sudo -u postgres createuser --pwprompt --no-superuser --no-createdb --no-createrole llama_test
sudo -u postgres createdb --owner=llama_test llama_test
sudo -u postgres psql -c 'REVOKE ALL ON DATABASE llama FROM PUBLIC'
sudo -u postgres psql -c 'REVOKE ALL ON DATABASE llama_test FROM PUBLIC'
```

Use distinct generated passwords at the interactive prompts. On the fresh cluster,
set `listen_addresses='127.0.0.1'`, `max_connections=30`,
`password_encryption='scram-sha-256'`, and `timezone='UTC'` in a new
`/etc/postgresql/18/main/conf.d/llama-local.conf`. The Ubuntu loopback host rules
must use `scram-sha-256`. Restart only this new cluster after checking configuration.
This installation created those settings; it did not modify a preexisting cluster.

Create an ignored mode-0600 environment file with exported
`LLAMA_DATABASE_URL=postgresql+psycopg://llama_app:PASSWORD@127.0.0.1:5432/llama`
and `LLAMA_TEST_DATABASE_URL=postgresql+psycopg://llama_test:OTHER_PASSWORD@127.0.0.1:5432/llama_test`.
Percent-encode reserved URL characters in passwords. The generated passwords on
this VPS use hexadecimal characters, so no URL escaping is needed.

## Verify and maintain

```bash
ruff check .
ruff format --check .
pytest
source .runtime/database.env
unset LLAMA_DATABASE_URL
pytest --run-database
pytest --run-database --run-inference
```

Unsetting the application URL keeps unrelated ordinary test applications from
connecting to real application data. Database fixtures inject their own test
schema. Missing test credentials fail an explicitly requested database run;
they do not quietly skip it. Both real services are opt-in in the ordinary suite.

Preview retention against application data:

```bash
source .runtime/database.env
python -m backend.retention --days 30
```

Output reports eligible old conversations and expired sessions separately; their
cascaded children are not an additional count. `--apply` actually deletes these
rows and their children. Review the preview and back up anything needed first.
Active conversations and sessions with any running descendant are protected under
row locks. Expiry is fixed at 30 days from session creation, not a sliding timeout;
`last_seen_at` records activity but does not extend the cookie. No automatic
retention schedule or backup service is configured in Phase 3.
