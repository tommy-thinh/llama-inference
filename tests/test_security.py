"""Independent public-profile boundaries from PROJECT_SPEC Phase 4 and docs/10.

No PostgreSQL or inference server is contacted by these ordinary tests.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from backend.app import create_app
from backend.config import Settings

ORIGIN = "https://demo.example"
TEST_URL = "postgresql+psycopg://unused:unused@127.0.0.1/unused_test"


@pytest.fixture
def fixed_rate_window(monkeypatch):
    monkeypatch.setattr("backend.resources.time", SimpleNamespace(monotonic=lambda: 60.0))


def public_settings(**overrides):
    return Settings(
        **{
            "public_origin": ORIGIN,
            "database_url": TEST_URL,
            "session_cookie_secure": True,
            **overrides,
        }
    )


class NoDatabase:
    def __init__(self, url):
        pass

    async def recover(self):
        pass

    async def close(self):
        pass

    async def ping(self):
        return True


@asynccontextmanager
async def public_client(monkeypatch, *, peer="127.0.0.1", handler=None, **settings):
    monkeypatch.setattr("backend.app.Database", NoDatabase)
    app = create_app(
        public_settings(**settings),
        transport=httpx.MockTransport(handler or (lambda r: httpx.Response(200))),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(peer, 1234)),
            base_url=ORIGIN,
            headers={"origin": ORIGIN},
        ) as client:
            yield app, client


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_url": None},
        {"session_cookie_secure": False},
        {"public_origin": "http://demo.example"},
        {"public_origin": "https://demo.example/path"},
        {"public_origin": "https://user:secret@demo.example"},
        {"public_origin": "https://demo.example?query=secret"},
        {"public_origin": "https://demo.example#fragment"},
    ],
)
def test_public_settings_fail_closed(overrides):
    with pytest.raises(ValueError):
        public_settings(**overrides)


def test_public_origin_environment_is_opt_in(monkeypatch):
    monkeypatch.setenv("LLAMA_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("LLAMA_DATABASE_URL", TEST_URL)
    monkeypatch.setenv("LLAMA_SESSION_COOKIE_SECURE", "true")
    settings = Settings.from_environment()
    assert settings.public_origin == ORIGIN
    monkeypatch.delenv("LLAMA_PUBLIC_ORIGIN")
    assert Settings.from_environment().public_origin is None


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
@pytest.mark.parametrize("origin", [None, "https://attacker.example", "null", ORIGIN + ".evil"])
def test_public_writes_require_exact_origin(monkeypatch, method, origin):
    async def check():
        async with public_client(monkeypatch) as (_, client):
            headers = {"content-type": "application/json"}
            if origin is None:
                del client.headers["origin"]
            else:
                headers["origin"] = origin
            response = await client.request(
                method, "/api/not-a-route", headers=headers, content="{}"
            )
            assert response.status_code == 403
            assert "access-control-allow-origin" not in response.headers

    asyncio.run(check())


def test_host_fetch_metadata_and_media_type_guards(monkeypatch):
    async def check():
        async with public_client(monkeypatch) as (_, client):
            assert (
                await client.get("/health", headers={"host": "attacker.example"})
            ).status_code in {
                400,
                403,
            }
            for site in ("cross-site",):
                response = await client.post(
                    "/api/not-a-route", headers={"sec-fetch-site": site}, json={}
                )
                assert response.status_code == 403
            for content_type in ("text/plain", "application/x-www-form-urlencoded"):
                response = await client.post(
                    "/api/not-a-route", headers={"content-type": content_type}, content="{}"
                )
                assert response.status_code == 415
            assert (await client.post("/api/not-a-route", json={})).status_code == 404

    asyncio.run(check())


def test_public_disables_transient_chat_and_documentation(monkeypatch):
    calls = []

    async def check():
        async with public_client(monkeypatch, handler=lambda r: calls.append(r)) as (_, client):
            response = await client.post(
                "/api/chat", json={"messages": [{"role": "user", "content": "SECRET"}]}
            )
            assert response.status_code in {404, 410}
            for path in ("/docs", "/redoc", "/openapi.json"):
                assert (await client.get(path)).status_code == 404
        assert calls == []

    asyncio.run(check())


@pytest.mark.parametrize("chunked", [False, True])
def test_public_body_cap_precedes_json_parsing(monkeypatch, chunked):
    async def body():
        for _ in range(9):
            yield b"SECRET" * 1500

    async def check():
        async with public_client(monkeypatch) as (_, client):
            response = await client.post(
                "/api/not-a-route",
                content=body() if chunked else b"SECRET" * 12000,
                headers={"content-type": "application/json"},
            )
            assert response.status_code == 413
            assert "SECRET" not in response.text
            accepted = await client.post(
                "/api/not-a-route",
                content=b" " * (64 * 1024),
                headers={"content-type": "application/json"},
            )
            assert accepted.status_code != 413

    asyncio.run(check())


@pytest.mark.parametrize("peer", ["198.51.100.10", "127.0.0.1"])
def test_forwarding_headers_cannot_rotate_untrusted_identity(monkeypatch, peer, fixed_rate_window):
    async def check():
        async with public_client(monkeypatch, peer=peer) as (_, client):
            for index in range(60):
                headers = {"x-forwarded-for": f"203.0.113.{index}", "forwarded": f"for={index}"}
                if peer != "127.0.0.1":
                    headers["x-real-ip"] = f"203.0.113.{index}"
                assert (await client.get("/api/not-a-route", headers=headers)).status_code == 404
            response = await client.get("/api/not-a-route")
            assert response.status_code == 429
            assert int(response.headers["retry-after"]) > 0
            assert (await client.get("/health")).status_code == 200

    asyncio.run(check())


def test_loopback_proxy_overwritten_real_ip_separates_clients(monkeypatch, fixed_rate_window):
    async def check():
        async with public_client(monkeypatch) as (_, client):
            for _ in range(60):
                assert (
                    await client.get("/api/not-a-route", headers={"x-real-ip": "198.51.100.1"})
                ).status_code == 404
            assert (
                await client.get("/api/not-a-route", headers={"x-real-ip": "198.51.100.1"})
            ).status_code == 429
            assert (
                await client.get("/api/not-a-route", headers={"x-real-ip": "198.51.100.2"})
            ).status_code == 404

    asyncio.run(check())


def test_public_errors_have_security_headers_and_private_json_logs(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    async def check():
        async with public_client(monkeypatch) as (_, client):
            response = await client.post(
                "/api/SECRET_PATH?query=SECRET_QUERY",
                content="SECRET_PROMPT",
                headers={
                    "content-type": "application/json",
                    "cookie": "llama_session=SECRET_COOKIE",
                    "x-real-ip": "198.51.100.77",
                    "x-request-id": "SECRET_REQUEST_ID",
                },
            )
            assert response.status_code == 404
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["referrer-policy"] in {"no-referrer", "same-origin"}
            assert response.headers.get(
                "x-frame-options"
            ) == "DENY" or "frame-ancestors 'none'" in (
                response.headers.get("content-security-policy", "")
            )
            request_id = response.headers["x-request-id"]
            assert request_id and request_id != "SECRET_REQUEST_ID"

        records = []
        for record in caplog.records:
            try:
                value = json.loads(record.getMessage())
            except ValueError:
                continue
            if isinstance(value, dict) and value.get("request_id") == request_id:
                records.append(value)
        assert records, "No structured application request record"
        encoded = json.dumps(records)
        assert "SECRET" not in encoded
        assert "198.51.100.77" not in encoded
        assert any(record.get("method") == "POST" for record in records)
        assert any(record.get("status") == 404 for record in records)

    asyncio.run(check())


@pytest.mark.parametrize("native_status", [200, 503])
def test_readiness_checks_native_while_liveness_stays_cheap(monkeypatch, native_status):
    paths = []

    def upstream(request):
        paths.append(request.url.path)
        return httpx.Response(native_status, json={"status": "ok"})

    async def check():
        async with public_client(monkeypatch, handler=upstream) as (_, client):
            assert (await client.get("/health")).status_code == 200
            assert paths == []
            ready = await client.get("/ready")
            assert ready.status_code == (200 if native_status == 200 else 503)
            assert paths == ["/health"]

    asyncio.run(check())


def test_readiness_database_failure_is_safe(monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    async def unavailable(self):
        raise SQLAlchemyError("SECRET_DATABASE_DSN")

    monkeypatch.setattr(NoDatabase, "ping", unavailable)

    async def check():
        async with public_client(monkeypatch) as (_, client):
            assert (await client.get("/health")).status_code == 200
            response = await client.get("/ready")
            assert response.status_code == 503
            assert "SECRET" not in response.text

    asyncio.run(check())


def test_admission_fifo_saturation_timeout_and_cancel_cleanup():
    from fastapi import HTTPException

    from backend.resources import Admission

    async def check():
        admission = Admission(queue_size=2, wait_timeout=0.1)
        first = await admission.acquire()
        order = []

        async def waiter(label):
            permit = await admission.acquire()
            order.append(label)
            return permit

        one = asyncio.create_task(waiter("one"))
        await asyncio.sleep(0)
        two = asyncio.create_task(waiter("two"))
        await asyncio.sleep(0)
        assert admission.active == 1 and len(admission.waiting) == 2
        with pytest.raises(HTTPException) as full:
            await admission.acquire()
        assert full.value.status_code == 503
        await first.release()
        promoted_one = await one
        assert order == ["one"]
        assert admission.active == 1
        await promoted_one.release()
        promoted_two = await two
        assert order == ["one", "two"]
        with pytest.raises(HTTPException) as expired:
            await admission.acquire()
        assert expired.value.status_code == 503
        assert len(admission.waiting) == 0
        cancelled = asyncio.create_task(admission.acquire())
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert len(admission.waiting) == 0
        await promoted_two.release()
        assert admission.active == 0
        final = await admission.acquire()
        await final.release()
        assert admission.active == 0

    asyncio.run(check())


def test_cancelled_promoted_waiter_does_not_lose_the_only_permit():
    from backend.resources import Admission

    async def check():
        admission = Admission(queue_size=2, wait_timeout=1)
        permit = await admission.acquire()
        queued = asyncio.create_task(admission.acquire())
        await asyncio.sleep(0)
        # Promotion can race with client cancellation before acquire resumes.
        await permit.release()
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert admission.active == 0 and len(admission.waiting) == 0
        next_permit = await asyncio.wait_for(admission.acquire(), 0.1)
        await next_permit.release()

    asyncio.run(check())


def test_fixed_rate_windows_reset_and_tracking_is_bounded(monkeypatch):
    from fastapi import HTTPException

    from backend.resources import RateLimiter

    timestamp = [60.0]
    monkeypatch.setattr("backend.resources.time", SimpleNamespace(monotonic=lambda: timestamp[0]))
    limiter = RateLimiter(capacity=2)
    limiter.check("ip", "one", 2)
    limiter.check("ip", "one", 2)
    with pytest.raises(HTTPException) as limited:
        limiter.check("ip", "one", 2)
    assert limited.value.status_code == 429
    limiter.check("ip", "two", 2)
    with pytest.raises(HTTPException) as overloaded:
        limiter.check("ip", "three", 2)
    assert overloaded.value.status_code in {429, 503}
    assert len(limiter.windows) <= 2
    timestamp[0] = 120.0
    limiter.check("ip", "three", 2)
    limiter.check("ip", "one", 2)
    assert len(limiter.windows) <= 2


def test_readiness_hung_dependency_is_bounded(monkeypatch):
    closed = asyncio.Event()

    async def unavailable(request):
        try:
            await asyncio.Future()
        finally:
            closed.set()

    async def check():
        async with public_client(monkeypatch, handler=unavailable) as (_, client):
            response = await asyncio.wait_for(client.get("/ready"), 3)
            assert response.status_code == 503
            assert closed.is_set()
            assert (await client.get("/health")).status_code == 200

    asyncio.run(check())


@pytest.mark.parametrize("disconnect", [True, False])
def test_request_admission_handoff_disconnect_or_cancel_returns_permit(disconnect):
    from backend.resources import Admission

    async def check():
        admission = Admission(queue_size=2, wait_timeout=1)
        first = await admission.acquire()
        disconnected = asyncio.Event()

        async def receive():
            await disconnected.wait()
            return {"type": "http.disconnect"}

        caller = asyncio.create_task(admission.for_request(SimpleNamespace(receive=receive)))
        async with asyncio.timeout(1):
            while not admission.waiting:
                await asyncio.sleep(0)
        await first.release()
        if disconnect:
            disconnected.set()
        else:
            caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert admission.active == 0 and len(admission.waiting) == 0
        permit = await asyncio.wait_for(admission.acquire(), 0.1)
        await permit.release()

    asyncio.run(check())


def test_backend_launcher_keeps_proxy_trust_and_process_limits_explicit(monkeypatch):
    from backend.__main__ import main

    calls = []
    monkeypatch.setattr("sys.argv", ["backend", "--port", "8123"])
    monkeypatch.setattr(
        "backend.__main__.uvicorn.run", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    main()
    assert len(calls) == 1
    args, options = calls[0]
    assert args == ("backend.app:app",)
    assert options["host"] == "127.0.0.1"
    assert options["port"] == 8123
    assert options["workers"] == 1
    assert options["proxy_headers"] is False
    assert options["access_log"] is False
    assert 1 <= options["limit_concurrency"] <= 128
    assert 0 < options["timeout_graceful_shutdown"] <= 30
    # A successful request-count exit would remain stopped under Restart=on-failure.
    assert options.get("limit_max_requests") is None
