"""Independent Phase 3 contracts from PROJECT_SPEC and docs/07–08.

Only an explicitly configured *_test PostgreSQL database is used. Every test
migrates its own temporary schema and drops only that schema on completion.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie

import httpx
import pytest

from backend.app import create_app
from backend.config import Settings

DONE = b"data: [DONE]\n\n"
TEXT = b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
USAGE = (
    b'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":2,"total_tokens":9},'
    b'"timings":{"predicted_per_second":12.5}}\n\n'
)


@pytest.fixture
def database_schema(monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool

    raw_url = os.environ.get("LLAMA_TEST_DATABASE_URL")
    if not raw_url:
        pytest.fail(
            "--run-database requires LLAMA_TEST_DATABASE_URL for a dedicated *_test database"
        )
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_test"):
        pytest.fail("Refusing database tests: require a PostgreSQL database ending in _test")
    schema = "test_" + uuid.uuid4().hex
    admin = create_engine(url, poolclass=NullPool)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("LLAMA_DATABASE_URL", isolated_url.render_as_string(hide_password=False))
    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url", isolated_url.render_as_string(hide_password=False).replace("%", "%%")
    )
    engine = create_engine(isolated_url, poolclass=NullPool)
    try:
        command.upgrade(config, "head")
        yield isolated_url, engine, config
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def rows(database_schema, sql, parameters=None):
    from sqlalchemy import text

    with database_schema[1].connect() as connection:
        return [dict(row) for row in connection.execute(text(sql), parameters or {}).mappings()]


def execute(database_schema, sql, parameters=None):
    from sqlalchemy import text

    with database_schema[1].begin() as connection:
        connection.execute(text(sql), parameters or {})


def reply(request):
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=TEXT + USAGE + DONE
    )


@asynccontextmanager
async def application(database_schema, handler=reply, **settings):
    app = create_app(
        Settings(database_url=database_schema[0], **settings),
        transport=httpx.MockTransport(handler),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield app, client


async def conversation(client, title="New conversation"):
    assert (await client.post("/api/session")).status_code == 200
    response = await client.post("/api/conversations", json={"title": title})
    assert response.status_code in {200, 201}, response.text
    return response.json()["id"]


def stream_events(response):
    return [
        data
        for block in response.text.split("\n\n")
        if (data := "\n".join(line[6:] for line in block.splitlines() if line.startswith("data: ")))
    ]


@pytest.mark.database
def test_migration_roundtrip_on_empty_isolated_schema(database_schema):
    from alembic import command

    tables = {
        r["table_name"]
        for r in rows(
            database_schema,
            "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()",
        )
    }
    assert {"sessions", "conversations", "messages", "inference_requests"} <= tables
    command.downgrade(database_schema[2], "base")
    remaining = {
        r["table_name"]
        for r in rows(
            database_schema,
            "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()",
        )
    }
    assert not ({"sessions", "conversations", "messages", "inference_requests"} & remaining)
    command.upgrade(database_schema[2], "head")
    assert rows(database_schema, "SELECT version_num FROM alembic_version")


@pytest.mark.database
def test_cookie_is_hashed_expiring_and_reused(database_schema):
    async def check():
        async with application(database_schema) as (_, client):
            response = await client.post("/api/session")
            assert response.status_code == 200
            cookie = SimpleCookie()
            cookie.load(response.headers["set-cookie"])
            assert len(cookie) == 1
            name, morsel = next(iter(cookie.items()))
            token = morsel.value
            assert len(token) >= 43  # 256 bits encoded as unpadded URL-safe base64.
            assert morsel["httponly"]
            assert morsel["samesite"].lower() == "lax"
            assert morsel["path"] == "/api"
            assert int(morsel["max-age"]) == 30 * 24 * 3600
            assert not morsel["secure"]
            stored = rows(database_schema, "SELECT * FROM sessions")
            assert len(stored) == 1
            assert stored[0]["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
            assert token not in response.text and stored[0]["token_hash"] not in response.text
            assert stored[0]["expires_at"] > stored[0]["created_at"]
            assert (await client.post("/api/session")).status_code == 200
            assert len(rows(database_schema, "SELECT * FROM sessions")) == 1
            execute(database_schema, "UPDATE sessions SET expires_at=now()-interval '1 second'")
            assert (await client.get("/api/conversations")).status_code == 401
            assert (await client.post("/api/session")).status_code == 200
            assert client.cookies[name] != token
            assert len(rows(database_schema, "SELECT * FROM sessions")) == 2

    asyncio.run(check())


@pytest.mark.database
def test_crud_pagination_and_uniform_owner_isolation(database_schema):
    async def check():
        async with application(database_schema) as (app, owner):
            assert (await owner.get("/api/conversations")).status_code == 401
            first = await conversation(owner, "First")
            second = await conversation(owner, "Second")
            result = (await owner.get("/api/conversations?limit=1&offset=0")).json()
            assert result["limit"] == 1 and result["offset"] == 0
            assert [item["id"] for item in result["items"]] == [second]
            assert (await owner.get("/api/conversations?limit=0")).status_code == 422
            assert (await owner.get("/api/conversations?offset=-1")).status_code == 422
            assert (
                await owner.patch(f"/api/conversations/{first}", json={"title": "Renamed"})
            ).status_code == 200
            assert (await owner.get(f"/api/conversations/{first}")).json()["title"] == "Renamed"
            assert (
                await owner.patch(f"/api/conversations/{first}", json={"title": "  "})
            ).status_code == 422
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as stranger:
                assert (await stranger.post("/api/session")).status_code == 200
                for identifier in (first, str(uuid.uuid4())):
                    for method, suffix, body in (
                        ("GET", "", None),
                        ("PATCH", "", {"title": "Stolen"}),
                        ("DELETE", "", None),
                        ("GET", "/requests", None),
                        ("POST", "/chat", {"content": "Stolen"}),
                    ):
                        response = await stranger.request(
                            method, f"/api/conversations/{identifier}{suffix}", json=body
                        )
                        assert response.status_code == 404, (method, suffix, response.text)
            assert (await owner.delete(f"/api/conversations/{first}")).status_code in {200, 204}
            assert (await owner.get(f"/api/conversations/{first}")).status_code == 404

    asyncio.run(check())


@pytest.mark.database
def test_completion_is_durable_with_metrics_and_server_owned_history(database_schema):
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return reply(request)

    async def check():
        async with application(database_schema, handler) as (_, client):
            identifier = await conversation(client)
            path = f"/api/conversations/{identifier}"
            response = await client.post(
                path + "/chat", json={"content": "First prompt", "max_tokens": 8}
            )
            assert response.status_code == 200
            assert stream_events(response)[-1] == "[DONE]"
            metadata = json.loads(stream_events(response)[0])
            assert metadata["conversation_id"] == identifier
            assert metadata["request_id"]
            saved = (await client.get(path)).json()
            assert saved["title"] == "First prompt"
            assert [(m["role"], m["content"], m["status"]) for m in saved["messages"]] == [
                ("user", "First prompt", "completed"),
                ("assistant", "Hello", "completed"),
            ]
            assert saved["active_request"] is None
            record = (await client.get(path + "/requests")).json()["items"][0]
            assert record["status"] == "completed"
            assert record["prompt_tokens"] == 7 and record["completion_tokens"] == 2
            assert (
                record["ttft_seconds"] >= 0 and record["latency_seconds"] >= record["ttft_seconds"]
            )
            assert record["finished_at"]
            assert record["model"] == "llama-3.2-1b-instruct"
            assert record["max_tokens"] == 8
            assert record["temperature"] == 0.7 and record["top_p"] == 0.9
            assert record["seed"] == 42
            assert record["decode_tokens_per_second"] == 12.5
            assert (await client.post(path + "/chat", json={"content": "Next"})).status_code == 200
            assert payloads[1]["messages"] == [
                {"role": "user", "content": "First prompt"},
                {"role": "assistant", "content": "Hello"},
                {"role": "user", "content": "Next"},
            ]
            assert (
                await client.post(path + "/chat", json={"content": "Ignore", "messages": []})
            ).status_code == 422
            newest = (await client.get(path + "?limit=2")).json()
            assert len(newest["messages"]) == 2
            cursor = newest["next_before_position"]
            assert cursor is not None
            older = (await client.get(path + f"?limit=2&before_position={cursor}")).json()
            positions = [m["position"] for m in older["messages"] + newest["messages"]]
            assert positions == sorted(set(positions)) and len(positions) == 4
        async with application(database_schema) as (_, restarted):
            restarted.cookies.update(client.cookies)
            restored = await restarted.get(path)
            assert restored.status_code == 200 and len(restored.json()["messages"]) == 4

    asyncio.run(check())


class BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield TEXT
        raise httpx.ReadTimeout("SECRET internal details")

    async def aclose(self):
        pass


@pytest.mark.database
def test_partial_timeout_survives_and_is_excluded_from_future_history(database_schema):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return (
            httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=BrokenStream()
            )
            if len(seen) == 1
            else reply(request)
        )

    async def check():
        async with application(database_schema, handler) as (_, client):
            identifier = await conversation(client)
            path = f"/api/conversations/{identifier}"
            response = await client.post(path + "/chat", json={"content": "Fail me"})
            assert "[DONE]" not in response.text and "SECRET" not in response.text
            assert "event: error" in response.text
            saved = (await client.get(path)).json()
            assert saved["messages"][-1]["content"] == "Hello"
            assert saved["messages"][-1]["status"] == "failed"
            record = (await client.get(path + "/requests")).json()["items"][0]
            assert record["status"] == "failed" and record["finished_at"]
            assert record["error_code"]
            assert (
                await client.post(path + "/chat", json={"content": "Recover"})
            ).status_code == 200
            assert seen[1]["messages"] == [{"role": "user", "content": "Recover"}]

    asyncio.run(check())


@pytest.mark.database
def test_one_active_turn_is_committed_before_upstream_and_races_return_409(database_schema):
    async def check():
        started = asyncio.Event()
        finish = asyncio.Event()
        calls = []

        async def handler(request):
            calls.append(request)
            started.set()
            await finish.wait()
            return reply(request)

        async with application(database_schema, handler) as (_, client):
            identifier = await conversation(client)
            path = f"/api/conversations/{identifier}"
            task = asyncio.create_task(client.post(path + "/chat", json={"content": "First"}))
            try:
                await asyncio.wait_for(started.wait(), 3)
                assert (
                    len(
                        rows(
                            database_schema,
                            "SELECT * FROM inference_requests WHERE status='running'",
                        )
                    )
                    == 1
                )
                messages = rows(database_schema, "SELECT * FROM messages ORDER BY position")
                assert len(messages) == 2 and messages[-1]["status"] == "partial"
                assert (
                    await client.post(path + "/chat", json={"content": "Race"})
                ).status_code == 409
                assert (await client.delete(path)).status_code == 409
                assert len(calls) == 1
                assert len(rows(database_schema, "SELECT * FROM messages")) == 2
            finally:
                finish.set()
                await asyncio.wait_for(task, 3)

    asyncio.run(check())


@pytest.mark.parametrize("asgi_version", ["2.3", "2.4"])
@pytest.mark.database
def test_disconnect_preserves_checkpoint_and_cancelled_state(database_schema, asgi_version):
    async def check():
        delivered = asyncio.Event()
        disconnect = asyncio.Event()
        closed = asyncio.Event()
        wait_forever = asyncio.Event()

        class ContinuingStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for _ in range(5):
                    yield TEXT
                    await asyncio.sleep(0.09)
                delivered.set()
                await wait_forever.wait()

            async def aclose(self):
                closed.set()

        def handler(request):
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=ContinuingStream()
            )

        async with application(database_schema, handler) as (app, client):
            identifier = await conversation(client)
            path = f"/api/conversations/{identifier}"
            sent = []
            request_sent = False

            async def receive():
                nonlocal request_sent
                if not request_sent:
                    request_sent = True
                    return {
                        "type": "http.request",
                        "body": b'{"content":"Stop this"}',
                        "more_body": False,
                    }
                await disconnect.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)

            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": asgi_version},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": path + "/chat",
                "raw_path": (path + "/chat").encode(),
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"cookie", "; ".join(f"{k}={v}" for k, v in client.cookies.items()).encode()),
                ],
                "client": ("127.0.0.1", 1234),
                "server": ("127.0.0.1", 8000),
            }
            task = asyncio.create_task(app(scope, receive, send))
            try:
                await asyncio.wait_for(delivered.wait(), 3)
                checkpoint = rows(
                    database_schema, "SELECT content,status FROM messages WHERE role='assistant'"
                )
                assert checkpoint[0]["content"].startswith("Hello")
                assert checkpoint[0]["status"] == "partial"
            finally:
                disconnect.set()
                await asyncio.wait_for(task, 6)
            assert closed.is_set()
            saved = (await client.get(path)).json()
            assert saved["messages"][-1]["content"] == "Hello" * 5
            assert saved["messages"][-1]["status"] == "cancelled"
            record = rows(database_schema, "SELECT status,finished_at FROM inference_requests")[0]
            assert record["status"] == "cancelled" and record["finished_at"]
            assert b"[DONE]" not in b"".join(m.get("body", b"") for m in sent)

    asyncio.run(check())


@pytest.mark.database
def test_restart_recovers_orphaned_running_turn_without_losing_text(database_schema):
    async def check():
        async with application(database_schema) as (_, client):
            identifier = await conversation(client)
            path = f"/api/conversations/{identifier}"
            assert (
                await client.post(path + "/chat", json={"content": "Interrupted"})
            ).status_code == 200
            cookie = dict(client.cookies)
        execute(database_schema, "UPDATE inference_requests SET status='running', finished_at=NULL")
        execute(database_schema, "UPDATE messages SET status='partial' WHERE role='assistant'")
        async with application(database_schema) as (_, restarted):
            restarted.cookies.update(cookie)
            saved = (await restarted.get(path)).json()
            assert saved["active_request"] is None
            assert saved["messages"][-1]["content"] == "Hello"
            assert saved["messages"][-1]["status"] == "failed"
            record = (await restarted.get(path + "/requests")).json()["items"][0]
            assert record["status"] == "failed" and record["finished_at"]
            assert record["error_code"]
            assert (
                await restarted.post(path + "/chat", json={"content": "Recovered"})
            ).status_code == 200

    asyncio.run(check())


@pytest.mark.database
def test_database_rejects_invalid_states_positions_and_metrics(database_schema):
    from sqlalchemy.exc import DataError, IntegrityError

    async def seed():
        async with application(database_schema) as (_, client):
            identifier = await conversation(client)
            assert (
                await client.post(f"/api/conversations/{identifier}/chat", json={"content": "Seed"})
            ).status_code == 200

    asyncio.run(seed())
    for statement in (
        "UPDATE messages SET status='invented'",
        "UPDATE messages SET role='system'",
        "UPDATE messages SET position=-1",
        "UPDATE messages SET position=0",
        "UPDATE messages SET role='user'",
        "UPDATE messages SET conversation_id='00000000-0000-0000-0000-000000000000'",
        "UPDATE messages SET request_id='00000000-0000-0000-0000-000000000000'",
        "UPDATE conversations SET session_id='00000000-0000-0000-0000-000000000000'",
        "UPDATE inference_requests SET conversation_id='00000000-0000-0000-0000-000000000000'",
        "UPDATE inference_requests SET status='invented'",
        "UPDATE inference_requests SET prompt_tokens=-1",
        "UPDATE inference_requests SET completion_tokens=-1",
        "UPDATE inference_requests SET ttft_seconds=-1",
        "UPDATE inference_requests SET latency_seconds=-1",
        "UPDATE inference_requests SET decode_tokens_per_second=-1",
        "UPDATE conversations SET next_position=0",
        "UPDATE conversations SET title='   '",
        "UPDATE conversations SET title=repeat('x',81)",
    ):
        with pytest.raises((IntegrityError, DataError)):
            execute(database_schema, statement)
    assert len(rows(database_schema, "SELECT * FROM messages")) == 2
    running_insert = """
        INSERT INTO inference_requests
        (id,conversation_id,status,model,max_tokens,temperature,top_p,seed,started_at)
        SELECT :id,conversation_id,'running',model,max_tokens,temperature,top_p,seed,now()
        FROM inference_requests WHERE status='completed' LIMIT 1
    """
    execute(database_schema, running_insert, {"id": uuid.uuid4()})
    with pytest.raises(IntegrityError):
        execute(database_schema, running_insert, {"id": uuid.uuid4()})


@pytest.mark.database
def test_reservation_rolls_back_all_rows_when_assistant_insert_fails(database_schema):
    calls = []

    def handler(request):
        calls.append(request)
        return reply(request)

    async def check():
        async with application(database_schema, handler) as (_, client):
            identifier = await conversation(client)
            before = rows(database_schema, "SELECT next_position FROM conversations")[0][
                "next_position"
            ]
            execute(
                database_schema,
                """
                CREATE FUNCTION reject_assistant() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF NEW.role='assistant' THEN
                    RAISE EXCEPTION 'SECRET simulated insert failure';
                  END IF;
                  RETURN NEW;
                END; $$
            """,
            )
            execute(
                database_schema,
                "CREATE TRIGGER injected_failure BEFORE INSERT ON messages "
                "FOR EACH ROW EXECUTE FUNCTION reject_assistant()",
            )
            response = await client.post(
                f"/api/conversations/{identifier}/chat", json={"content": "Atomic"}
            )
            assert response.status_code == 503
            assert "SECRET" not in response.text
            assert rows(database_schema, "SELECT * FROM messages") == []
            assert rows(database_schema, "SELECT * FROM inference_requests") == []
            assert (
                rows(database_schema, "SELECT next_position FROM conversations")[0]["next_position"]
                == before
            )
            assert calls == []
            execute(database_schema, "DROP TRIGGER injected_failure ON messages")
            assert (
                await client.post(
                    f"/api/conversations/{identifier}/chat", json={"content": "Retry"}
                )
            ).status_code == 200

    asyncio.run(check())


@pytest.mark.database
def test_retention_dry_run_and_apply_preserve_running_owners(database_schema):
    from backend.database import Database

    async def check():
        async with application(database_schema) as (_, client):
            expired = await conversation(client, "Expired")
            assert (
                await client.post(f"/api/conversations/{expired}/chat", json={"content": "Old"})
            ).status_code == 200
            client.cookies.clear()
            old = await conversation(client, "Old conversation")
            client.cookies.clear()
            protected = await conversation(client, "Active expired session")
            assert (
                await client.post(
                    f"/api/conversations/{protected}/chat", json={"content": "Running"}
                )
            ).status_code == 200
        execute(
            database_schema,
            "UPDATE conversations SET updated_at=now()-interval '40 days' WHERE id=:id",
            {"id": old},
        )
        execute(
            database_schema,
            "UPDATE sessions SET expires_at=now()-interval '1 day' "
            "WHERE id IN (SELECT session_id FROM conversations "
            "WHERE id IN (:expired,:protected))",
            {"expired": expired, "protected": protected},
        )
        execute(
            database_schema,
            "UPDATE inference_requests SET status='running', finished_at=NULL "
            "WHERE conversation_id=:id",
            {"id": protected},
        )
        execute(
            database_schema,
            "UPDATE messages SET status='partial' WHERE conversation_id=:id AND role='assistant'",
            {"id": protected},
        )
        database = Database(database_schema[0])
        try:
            await database.retain(days=30)
            assert len(rows(database_schema, "SELECT * FROM conversations")) == 3
            await database.retain(days=30, apply=True)
            remaining = rows(database_schema, "SELECT id FROM conversations")
            assert [str(row["id"]) for row in remaining] == [protected]
            assert len(rows(database_schema, "SELECT * FROM sessions")) == 2
            assert len(rows(database_schema, "SELECT * FROM inference_requests")) == 1
            assert len(rows(database_schema, "SELECT * FROM messages")) == 2
        finally:
            await database.engine.dispose()

    asyncio.run(check())


@pytest.mark.database
def test_https_configuration_sets_secure_cookie(database_schema):
    async def check():
        async with application(database_schema, session_cookie_secure=True) as (_, client):
            response = await client.post("/api/session")
            cookie = SimpleCookie()
            cookie.load(response.headers["set-cookie"])
            assert next(iter(cookie.values()))["secure"]

    asyncio.run(check())


@pytest.mark.database
def test_failed_final_commit_never_emits_success_and_preserves_checkpoint(database_schema):
    async def check():
        async with application(database_schema) as (_, client):
            identifier = await conversation(client)
            execute(
                database_schema,
                """
                CREATE FUNCTION reject_terminal_write() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF NEW.status <> OLD.status THEN
                    RAISE EXCEPTION 'SECRET database terminal writes unavailable';
                  END IF;
                  RETURN NEW;
                END; $$
            """,
            )
            execute(
                database_schema,
                "CREATE TRIGGER terminal_write_failure BEFORE UPDATE ON inference_requests "
                "FOR EACH ROW EXECUTE FUNCTION reject_terminal_write()",
            )
            response = await client.post(
                f"/api/conversations/{identifier}/chat", json={"content": "Commit failure"}
            )
            assert response.status_code == 200
            assert "persistence_error" in response.text and "event: error" in response.text
            assert "[DONE]" not in response.text and "SECRET" not in response.text
            stored = rows(
                database_schema, "SELECT content,status FROM messages WHERE role='assistant'"
            )[0]
            assert stored == {"content": "Hello", "status": "partial"}
            assert (
                rows(database_schema, "SELECT status FROM inference_requests")[0]["status"]
                == "running"
            )

    asyncio.run(check())


@pytest.mark.parametrize("mode", ["headers_disconnect", "headers_send_error", "headers_stall"])
@pytest.mark.parametrize("asgi_version", ["2.3", "2.4"])
@pytest.mark.database
def test_reservation_is_finalized_when_client_cannot_receive_headers(
    database_schema, mode, asgi_version
):
    async def check():
        disconnect = asyncio.Event()
        calls = []

        def handler(request):
            calls.append(request)
            return reply(request)

        async with application(database_schema, handler, total_timeout=0.15) as (app, client):
            identifier = await conversation(client)
            path = f"/api/conversations/{identifier}/chat"
            sent_request = False

            async def receive():
                nonlocal sent_request
                if not sent_request:
                    sent_request = True
                    return {
                        "type": "http.request",
                        "body": b'{"content":"Early stop"}',
                        "more_body": False,
                    }
                await disconnect.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                if message["type"] == "http.response.start":
                    if mode == "headers_send_error":
                        raise OSError("Client socket closed")
                    if mode == "headers_disconnect":
                        disconnect.set()
                    await asyncio.Event().wait()

            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": asgi_version},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"cookie", "; ".join(f"{k}={v}" for k, v in client.cookies.items()).encode()),
                ],
                "client": ("127.0.0.1", 1234),
                "server": ("127.0.0.1", 8000),
            }
            await asyncio.wait_for(app(scope, receive, send), 6)
            request = rows(database_schema, "SELECT status,finished_at FROM inference_requests")[0]
            assert request["status"] == "cancelled" and request["finished_at"]
            message = rows(
                database_schema, "SELECT status,content FROM messages WHERE role='assistant'"
            )[0]
            assert message == {"status": "cancelled", "content": ""}
            assert calls == []
            assert (await client.get(f"/api/conversations/{identifier}")).json()[
                "active_request"
            ] is None

    asyncio.run(check())


@pytest.mark.inference
@pytest.mark.database
def test_real_llama_turn_is_persisted_and_restored(database_schema, live_server):
    async def check():
        settings = Settings(
            database_url=database_schema[0], upstream_url=live_server.config.base_url
        )
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                identifier = await conversation(client)
                path = f"/api/conversations/{identifier}"
                response = await client.post(
                    path + "/chat",
                    json={
                        "content": "What is 2 + 2? Reply with only the number.",
                        "max_tokens": 24,
                        "temperature": 0,
                    },
                )
                assert stream_events(response)[-1] == "[DONE]"
                saved = (await client.get(path)).json()
                assert saved["messages"][-1]["status"] == "completed"
                assert "4" in saved["messages"][-1]["content"]
                request = (await client.get(path + "/requests")).json()["items"][0]
                assert request["status"] == "completed"
                assert request["completion_tokens"] > 0 and request["prompt_tokens"] > 0
                assert request["latency_seconds"] >= request["ttft_seconds"] >= 0
                cookie = dict(client.cookies)
        restarted = create_app(settings)
        async with restarted.router.lifespan_context(restarted):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=restarted), base_url="http://test", cookies=cookie
            ) as client:
                assert (await client.get(path)).json()["messages"] == saved["messages"]

    asyncio.run(check())


@pytest.mark.database
def test_invalid_persistent_turns_leave_no_history_or_upstream_work(database_schema):
    calls = []

    def handler(request):
        calls.append(request)
        return reply(request)

    async def check():
        async with application(database_schema, handler) as (_, client):
            identifier = await conversation(client)
            for payload in (
                {"content": "  "},
                {"content": "x" * 4001},
                {"content": "Valid", "max_tokens": 2049},
                {"content": "Valid", "temperature": 2.1},
                {"content": "Valid", "messages": []},
            ):
                response = await client.post(f"/api/conversations/{identifier}/chat", json=payload)
                assert response.status_code == 422
            assert rows(database_schema, "SELECT * FROM messages") == []
            assert rows(database_schema, "SELECT * FROM inference_requests") == []
            assert calls == []

    asyncio.run(check())


def test_persistence_configuration_uses_environment_and_hides_credentials(monkeypatch):
    url = "postgresql+psycopg://user:SECRET@127.0.0.1/llama_test"
    monkeypatch.setenv("LLAMA_DATABASE_URL", url)
    monkeypatch.setenv("LLAMA_SESSION_COOKIE_SECURE", "true")
    settings = Settings.from_environment()
    assert settings.database_url == url and settings.session_cookie_secure is True
    assert "SECRET" not in repr(settings)
    monkeypatch.setenv("LLAMA_SESSION_COOKIE_SECURE", "false")
    assert Settings.from_environment().session_cookie_secure is False
    monkeypatch.setenv("LLAMA_SESSION_COOKIE_SECURE", "sometimes")
    with pytest.raises(ValueError):
        Settings.from_environment()
    for invalid in (
        "sqlite:///ignored.db",
        "postgresql://user:SECRET@127.0.0.1/llama_test",
        "postgresql+psycopg://user:SECRET@example.com/llama_test",
    ):
        with pytest.raises(ValueError) as rejected:
            Settings(database_url=invalid)
        assert "SECRET" not in str(rejected.value)


def test_unconfigured_persistence_is_safe_503_and_transient_chat_still_works():
    async def check():
        app = create_app(Settings(database_url=None), transport=httpx.MockTransport(reply))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                for method, path in (
                    ("POST", "/api/session"),
                    ("GET", "/api/conversations"),
                    ("POST", "/api/conversations"),
                ):
                    response = await client.request(method, path)
                    assert response.status_code == 503
                    assert "SECRET" not in response.text
                response = await client.post(
                    "/api/chat", json={"messages": [{"role": "user", "content": "Transient"}]}
                )
                assert response.status_code == 200 and stream_events(response)[-1] == "[DONE]"

    asyncio.run(check())


def test_retention_cli_refuses_invalid_days_or_missing_credentials(monkeypatch, capsys):
    from backend import retention

    monkeypatch.delenv("LLAMA_DATABASE_URL", raising=False)
    monkeypatch.delenv("LLAMA_SESSION_COOKIE_SECURE", raising=False)
    for arguments in (["retention", "--days", "0", "--apply"], ["retention"]):
        monkeypatch.setattr(sys, "argv", arguments)
        with pytest.raises(SystemExit) as exit_status:
            retention.main()
        assert exit_status.value.code == 2
        assert "error:" in capsys.readouterr().err


@pytest.mark.database
def test_retention_cli_defaults_to_dry_run_then_requires_apply(database_schema):
    async def seed():
        async with application(database_schema) as (_, client):
            await conversation(client, "Eligible")

    asyncio.run(seed())
    execute(database_schema, "UPDATE conversations SET updated_at=now()-interval '40 days'")
    execute(database_schema, "UPDATE sessions SET expires_at=now()-interval '1 day'")
    environment = {
        **os.environ,
        "LLAMA_DATABASE_URL": database_schema[0].render_as_string(hide_password=False),
    }
    for options, apply in (([], False), (["--apply"], True)):
        result = subprocess.run(
            [sys.executable, "-m", "backend.retention", *options],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["applied"] is apply and report["sessions"] == 1
        assert len(rows(database_schema, "SELECT * FROM conversations")) == (0 if apply else 1)
    assert rows(database_schema, "SELECT * FROM sessions") == []


@pytest.mark.database
def test_total_deadline_during_checkpoint_is_failed_with_partial_text(database_schema):
    closed = False

    class ImmediateStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield TEXT
            yield DONE

        async def aclose(self):
            nonlocal closed
            closed = True

    def handler(request):
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=ImmediateStream()
        )

    async def check():
        async with application(database_schema, handler, total_timeout=0.05) as (_, client):
            identifier = await conversation(client)
            execute(
                database_schema,
                """
                CREATE FUNCTION slow_checkpoint() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF NEW.role='assistant' AND NEW.status='partial' THEN
                    PERFORM pg_sleep(0.2);
                  END IF;
                  RETURN NEW;
                END; $$
            """,
            )
            execute(
                database_schema,
                "CREATE TRIGGER slow_checkpoint BEFORE UPDATE ON messages "
                "FOR EACH ROW EXECUTE FUNCTION slow_checkpoint()",
            )
            response = await client.post(
                f"/api/conversations/{identifier}/chat", json={"content": "Slow save"}
            )
            assert "[DONE]" not in response.text
            assert "event: error" in response.text and "upstream_timeout" in response.text
            assert closed
            request = rows(
                database_schema, "SELECT status,error_code,finished_at FROM inference_requests"
            )[0]
            assert request["status"] == "failed" and request["error_code"] == "upstream_timeout"
            assert request["finished_at"]
            assistant = rows(
                database_schema, "SELECT content,status FROM messages WHERE role='assistant'"
            )[0]
            assert assistant == {"content": "Hello", "status": "failed"}

    asyncio.run(check())


@pytest.mark.database
def test_terminal_commit_timeout_is_persistence_error_after_generation_ends(
    database_schema, monkeypatch
):
    async def check():
        async with application(database_schema, total_timeout=0.05) as (app, client):
            identifier = await conversation(client)
            original_finish = app.state.database.finish

            async def slow_final_commit(identity, status, content, metrics, error_code=None):
                if status == "completed":
                    await asyncio.sleep(0.07)
                    raise TimeoutError("SECRET terminal commit deadline expired")
                await original_finish(identity, status, content, metrics, error_code)

            monkeypatch.setattr(app.state.database, "finish", slow_final_commit)
            response = await client.post(
                f"/api/conversations/{identifier}/chat", json={"content": "Final write timeout"}
            )
            assert "event: error" in response.text and "persistence_error" in response.text
            assert "upstream_timeout" not in response.text
            assert "[DONE]" not in response.text and "SECRET" not in response.text
            record = rows(database_schema, "SELECT status,error_code FROM inference_requests")[0]
            assert record == {"status": "failed", "error_code": "persistence_error"}
            assistant = rows(
                database_schema, "SELECT status,content FROM messages WHERE role='assistant'"
            )[0]
            assert assistant == {"status": "failed", "content": "Hello"}

    asyncio.run(check())


@pytest.mark.database
def test_all_terminal_write_attempts_share_one_bounded_cleanup_budget(database_schema):
    async def check():
        async with application(database_schema, total_timeout=10) as (_, client):
            identifier = await conversation(client)
            execute(
                database_schema,
                """
                CREATE FUNCTION stalled_terminal_write() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF NEW.status <> OLD.status THEN
                    PERFORM pg_sleep(6);
                  END IF;
                  RETURN NEW;
                END; $$
            """,
            )
            execute(
                database_schema,
                "CREATE TRIGGER stalled_terminal_write BEFORE UPDATE ON inference_requests "
                "FOR EACH ROW EXECUTE FUNCTION stalled_terminal_write()",
            )
            started = time.monotonic()
            response = await asyncio.wait_for(
                client.post(
                    f"/api/conversations/{identifier}/chat", json={"content": "Stalled final write"}
                ),
                7,
            )
            elapsed = time.monotonic() - started
            assert 4.5 <= elapsed < 6.5
            assert "persistence_error" in response.text and "[DONE]" not in response.text
            assistant = rows(
                database_schema, "SELECT status,content FROM messages WHERE role='assistant'"
            )[0]
            assert assistant == {"status": "partial", "content": "Hello"}
            assert (
                rows(database_schema, "SELECT status FROM inference_requests")[0]["status"]
                == "running"
            )

    asyncio.run(check())
