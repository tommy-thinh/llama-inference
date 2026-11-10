"""Public quotas and admission, derived independently from docs/10.

Database cases inherit the Phase 3 fixture's dedicated *_test database check and
per-test Alembic schema. Inference is mocked; no application data is touched.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
import test_persistence
from test_persistence import execute, reply, rows

from backend.app import create_app
from backend.config import Settings

database_schema = test_persistence.database_schema

ORIGIN = "https://demo.example"


@asynccontextmanager
async def public_application(database_schema, handler=reply, *, cookies=None, **overrides):
    settings = Settings(
        **{
            "database_url": database_schema[0],
            "session_cookie_secure": True,
            "public_origin": ORIGIN,
            "ip_requests_per_minute": 1000,
            "session_requests_per_minute": 1000,
            **overrides,
        }
    )
    app = create_app(settings, transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
            base_url=ORIGIN,
            headers={"origin": ORIGIN, "x-real-ip": "198.51.100.42"},
            cookies=cookies,
        ) as client:
            yield app, client


async def new_conversation(client):
    assert (await client.post("/api/session")).status_code == 200
    response = await client.post("/api/conversations", json={"title": "Quota test"})
    assert response.status_code in {200, 201}, response.text
    return response.json()["id"]


async def turn(client, identity, *, tokens=8, content="Hello"):
    return await client.post(
        f"/api/conversations/{identity}/chat", json={"content": content, "max_tokens": tokens}
    )


async def wait_until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


def test_public_resource_defaults_match_documented_capacity():
    settings = Settings()
    assert settings.ip_requests_per_minute == 60
    assert settings.session_requests_per_minute == 6
    assert settings.daily_session_requests == 50
    assert settings.daily_session_tokens == 32768
    assert settings.daily_ip_requests == 200
    assert settings.daily_ip_tokens == 131072
    assert settings.queue_size == 2
    assert settings.queue_wait_timeout == 10


@pytest.mark.database
def test_public_cookie_secure_and_session_minute_limit_cannot_use_new_conversations(
    database_schema,
    monkeypatch,
):
    monkeypatch.setattr("backend.resources.time", SimpleNamespace(monotonic=lambda: 60.0))
    calls = []

    def handler(request):
        calls.append(request)
        return reply(request)

    async def check():
        async with public_application(database_schema, handler, session_requests_per_minute=6) as (
            _,
            client,
        ):
            bootstrap = await client.post("/api/session")
            assert "Secure" in bootstrap.headers["set-cookie"]
            assert "HttpOnly" in bootstrap.headers["set-cookie"]
            for _ in range(6):
                assert (await turn(client, await new_conversation(client))).status_code == 200
            denied = await turn(client, await new_conversation(client))
            assert denied.status_code == 429
            assert int(denied.headers["retry-after"]) > 0
            assert len(calls) == 6
            assert len(rows(database_schema, "SELECT * FROM inference_requests")) == 6

    asyncio.run(check())


@pytest.mark.database
@pytest.mark.parametrize(
    "limits,tokens",
    [
        ({"daily_session_requests": 2}, 8),
        ({"daily_session_tokens": 16}, 8),
        ({"daily_ip_requests": 2}, 8),
        ({"daily_ip_tokens": 16}, 8),
    ],
)
def test_daily_boundaries_charge_requested_output_budget(database_schema, limits, tokens):
    calls = []

    def handler(request):
        calls.append(request)
        return reply(request)  # Runtime reports only two output tokens.

    async def check():
        async with public_application(database_schema, handler, **limits) as (_, client):
            for _ in range(2):
                assert (
                    await turn(client, await new_conversation(client), tokens=tokens)
                ).status_code == 200
            denied = await turn(client, await new_conversation(client), tokens=tokens)
            assert denied.status_code == 429
            assert int(denied.headers["retry-after"]) > 0
            assert len(calls) == 2
            stored = rows(database_schema, "SELECT * FROM daily_quotas")
            assert len(stored) == 2
            assert {row["scope"] for row in stored} == {"session", "ip"}
            assert all(row["requests"] == 2 and row["tokens"] == 16 for row in stored)
            assert all(row["day"] == datetime.now(UTC).date() for row in stored)
            assert "198.51.100.42" not in str(stored)
            assert list(client.cookies.values())[0] not in str(stored)
            assert len(rows(database_schema, "SELECT * FROM inference_requests")) == 2

    asyncio.run(check())


@pytest.mark.database
def test_new_session_cannot_reset_shared_ip_quota_and_rejection_rolls_back_session_charge(
    database_schema,
):
    async def check():
        async with public_application(database_schema, daily_ip_requests=1) as (_, client):
            first = await new_conversation(client)
            assert (await turn(client, first)).status_code == 200
            client.cookies.clear()
            second = await new_conversation(client)
            denied = await turn(client, second)
            assert denied.status_code == 429
            assert len(rows(database_schema, "SELECT * FROM inference_requests")) == 1
            stored = rows(database_schema, "SELECT * FROM daily_quotas")
            assert len(stored) == 2
            assert all(row["requests"] == 1 for row in stored)

    asyncio.run(check())


@pytest.mark.database
def test_daily_quota_reservation_is_atomic_across_concurrent_sessions(database_schema):
    async def check():
        async with public_application(database_schema, daily_ip_requests=1) as (app, first):
            one = await new_conversation(first)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=ORIGIN,
                headers={"origin": ORIGIN, "x-real-ip": "198.51.100.42"},
            ) as second:
                two = await new_conversation(second)
                results = await asyncio.gather(turn(first, one), turn(second, two))
                assert sorted(result.status_code for result in results) == [200, 429]
            assert len(rows(database_schema, "SELECT * FROM inference_requests")) == 1
            assert len(rows(database_schema, "SELECT * FROM messages")) == 2
            assert all(
                row["requests"] == 1 for row in rows(database_schema, "SELECT * FROM daily_quotas")
            )

    asyncio.run(check())


@pytest.mark.database
def test_failed_admission_stays_charged_and_quota_survives_backend_restart(database_schema):
    def unavailable(request):
        return httpx.Response(503, text="SECRET upstream failure")

    async def check():
        async with public_application(database_schema, unavailable, daily_session_requests=1) as (
            _,
            client,
        ):
            identity = await new_conversation(client)
            failed = await turn(client, identity)
            assert failed.status_code == 200 and "event: error" in failed.text
            assert "SECRET" not in failed.text
            cookie = dict(client.cookies)
            assert (
                rows(database_schema, "SELECT status FROM inference_requests")[0]["status"]
                == "failed"
            )
        async with public_application(
            database_schema, cookies=cookie, daily_session_requests=1
        ) as (_, restarted):
            assert (await turn(restarted, identity)).status_code == 429
            assert len(rows(database_schema, "SELECT * FROM inference_requests")) == 1

    asyncio.run(check())


@pytest.mark.database
def test_new_utc_day_gets_new_quota_without_resetting_old_evidence(database_schema):
    async def check():
        async with public_application(database_schema, daily_session_requests=1) as (_, client):
            identity = await new_conversation(client)
            assert (await turn(client, identity)).status_code == 200
            execute(database_schema, "UPDATE daily_quotas SET day=day - 1")
            assert (await turn(client, identity)).status_code == 200
            stored = rows(database_schema, "SELECT * FROM daily_quotas")
            assert len(stored) == 4
            assert len({row["day"] for row in stored}) == 2
            assert all(row["requests"] == 1 for row in stored)

    asyncio.run(check())


@pytest.mark.database
def test_one_active_two_fifo_waiters_and_saturation_never_calls_upstream(database_schema):
    async def check():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def held(request):
            calls.append(request)
            entered.set()
            await release.wait()
            return reply(request)

        async with public_application(database_schema, held) as (app, client):
            identities = [await new_conversation(client) for _ in range(4)]
            tasks = []
            try:
                tasks.append(asyncio.create_task(turn(client, identities[0], content="first")))
                await asyncio.wait_for(entered.wait(), 2)
                for index in (1, 2):
                    tasks.append(
                        asyncio.create_task(
                            turn(client, identities[index], content=f"queued{index}")
                        )
                    )
                    await wait_until(
                        lambda expected=index: len(app.state.admission.waiting) == expected
                    )
                assert len(calls) == 1
                rejected = await asyncio.wait_for(turn(client, identities[3]), 2)
                assert rejected.status_code == 503
                assert int(rejected.headers["retry-after"]) > 0
                assert len(calls) == 1
                release.set()
                responses = await asyncio.wait_for(asyncio.gather(*tasks), 3)
                assert all(response.status_code == 200 for response in responses)
                observed = [response.text for response in responses]
                assert all("[DONE]" in text for text in observed)
                # Each upstream starts only after the previous turn durably finishes.
                request_rows = rows(
                    database_schema, "SELECT * FROM inference_requests ORDER BY started_at"
                )
                completed = [row for row in request_rows if row["status"] == "completed"]
                assert [str(row["conversation_id"]) for row in completed] == identities[:3]
                assert len(calls) == 3
                assert [json.loads(call.content)["messages"][-1]["content"] for call in calls] == [
                    "first",
                    "queued1",
                    "queued2",
                ]
                assert app.state.admission.active == 0
                assert len(app.state.admission.waiting) == 0
            finally:
                release.set()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(check())


@pytest.mark.database
def test_queue_timeout_and_cancelled_waiter_release_capacity(database_schema):
    async def check():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def held(request):
            calls.append(request)
            entered.set()
            await release.wait()
            return reply(request)

        async with public_application(database_schema, held, queue_wait_timeout=0.1) as (
            app,
            client,
        ):
            identities = [await new_conversation(client) for _ in range(4)]
            active = asyncio.create_task(turn(client, identities[0]))
            queued = None
            try:
                await asyncio.wait_for(entered.wait(), 2)
                queued = asyncio.create_task(turn(client, identities[1]))
                await wait_until(lambda: len(app.state.admission.waiting) == 1)
                queued.cancel()
                with suppress(asyncio.CancelledError):
                    await queued
                await wait_until(lambda: len(app.state.admission.waiting) == 0)
                timed_out = await asyncio.wait_for(turn(client, identities[2]), 2)
                assert timed_out.status_code == 503
                assert int(timed_out.headers["retry-after"]) > 0
                assert len(calls) == 1
                assert len(app.state.admission.waiting) == 0
                cancelled = rows(
                    database_schema,
                    "SELECT status FROM inference_requests WHERE conversation_id=:id",
                    {"id": identities[1]},
                )
                assert cancelled and cancelled[0]["status"] == "cancelled"
                release.set()
                assert (await asyncio.wait_for(active, 2)).status_code == 200
                assert (await turn(client, identities[3])).status_code == 200
                assert len(calls) == 2
                assert app.state.admission.active == 0
            finally:
                release.set()
                for task in (active, queued):
                    if task is not None and not task.done():
                        task.cancel()
                await asyncio.gather(
                    *[task for task in (active, queued) if task], return_exceptions=True
                )

    asyncio.run(check())


@pytest.mark.database
def test_permit_is_held_through_durable_finalization(database_schema, monkeypatch):
    async def check():
        writing, release_write = asyncio.Event(), asyncio.Event()
        calls = []

        def handler(request):
            calls.append(request)
            return reply(request)

        async with public_application(database_schema, handler) as (app, client):
            identities = [await new_conversation(client) for _ in range(2)]
            database = app.state.database
            original_finish = database.finish
            first_write = True

            async def blocked_finish(*args):
                nonlocal first_write
                if first_write:
                    first_write = False
                    writing.set()
                    await release_write.wait()
                return await original_finish(*args)

            monkeypatch.setattr(database, "finish", blocked_finish)
            tasks = []
            try:
                tasks.append(asyncio.create_task(turn(client, identities[0])))
                await asyncio.wait_for(writing.wait(), 2)
                tasks.append(asyncio.create_task(turn(client, identities[1])))
                await wait_until(lambda: len(app.state.admission.waiting) == 1)
                assert len(calls) == 1
                assert app.state.admission.active == 1
                assert (
                    rows(database_schema, "SELECT status FROM inference_requests")[0]["status"]
                    == "running"
                )
                release_write.set()
                completed = await asyncio.wait_for(asyncio.gather(*tasks), 3)
                assert all("[DONE]" in response.text for response in completed)
                assert len(calls) == 2
                assert app.state.admission.active == 0
            finally:
                release_write.set()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(check())


@pytest.mark.database
def test_stream_completion_logs_durable_outcome_without_private_data(database_schema, caplog):
    caplog.set_level(logging.INFO, logger="llama.requests")

    async def check():
        async with public_application(database_schema) as (_, client):
            identity = await new_conversation(client)
            response = await turn(client, identity, content="SECRET_PROMPT")
            assert "[DONE]" in response.text
            request_id = response.headers["x-request-id"]
            records = [
                json.loads(record.getMessage())
                for record in caplog.records
                if record.name == "llama.requests"
            ]
            terminal = [record for record in records if record.get("event") == "stream_complete"]
            assert len(terminal) == 1
            assert terminal[0]["request_id"] == request_id
            assert terminal[0]["status"] == "completed"
            encoded = json.dumps(records)
            assert "SECRET_PROMPT" not in encoded
            assert "198.51.100.42" not in encoded
            assert list(client.cookies.values())[0] not in encoded
            assert identity not in encoded

    asyncio.run(check())


@pytest.mark.database
def test_permit_remains_held_until_upstream_stream_closes(database_schema):
    from test_persistence import DONE, TEXT

    async def check():
        closing, release_close = asyncio.Event(), asyncio.Event()
        calls = []

        class ClosingStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield TEXT
                yield DONE

            async def aclose(self):
                closing.set()
                await release_close.wait()

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    stream=ClosingStream(),
                )
            return reply(request)

        async with public_application(database_schema, handler) as (app, client):
            identities = [await new_conversation(client) for _ in range(2)]
            tasks = []
            try:
                tasks.append(asyncio.create_task(turn(client, identities[0])))
                await asyncio.wait_for(closing.wait(), 2)
                tasks.append(asyncio.create_task(turn(client, identities[1])))
                await wait_until(lambda: len(app.state.admission.waiting) == 1)
                assert len(calls) == 1 and app.state.admission.active == 1
                release_close.set()
                responses = await asyncio.wait_for(asyncio.gather(*tasks), 3)
                assert all("[DONE]" in response.text for response in responses)
                assert len(calls) == 2 and app.state.admission.active == 0
            finally:
                release_close.set()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(check())


@pytest.mark.database
def test_readiness_requires_migrated_quota_table_while_liveness_stays_healthy(database_schema):
    from alembic import command

    def native_health(request):
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok"})

    async def check():
        async with public_application(database_schema, native_health) as (_, client):
            assert (await client.get("/ready")).status_code == 200
            # Only this fixture's isolated *_test schema is downgraded.
            command.downgrade(database_schema[2], "0001")
            try:
                assert (await client.get("/health")).status_code == 200
                not_ready = await client.get("/ready")
                assert not_ready.status_code == 503
                assert "daily_quotas" not in not_ready.text
                assert "postgresql" not in not_ready.text
            finally:
                command.upgrade(database_schema[2], "head")
            assert (await client.get("/ready")).status_code == 200

    asyncio.run(check())
