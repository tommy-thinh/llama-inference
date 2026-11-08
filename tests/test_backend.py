"""Phase 2 API tests derived from docs/04 and the Phase 2 specification.

All upstream traffic uses HTTPX's transport seam; no model is loaded here.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from backend.app import create_app
from backend.config import Settings

PROMPT = {"messages": [{"role": "user", "content": "Hello"}]}
DONE = b"data: [DONE]\n\n"
TEXT = b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, chunks=(), *, error=None, delay=0):
        self.chunks = chunks
        self.error = error
        self.delay = delay
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


class TrackedTransport(httpx.MockTransport):
    def __init__(self, handler):
        super().__init__(handler)
        self.closed = False

    async def aclose(self):
        self.closed = True
        await super().aclose()


@asynccontextmanager
async def client_for(handler, settings=None) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings=settings, transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


def events(response):
    result = []
    for block in response.text.replace("\r\n", "\n").split("\n\n"):
        fields = block.splitlines()
        data = "\n".join(line[5:].lstrip(" ") for line in fields if line.startswith("data:"))
        if data:
            result.append(
                (
                    next(
                        (line[6:].strip() for line in fields if line.startswith("event:")),
                        "message",
                    ),
                    data,
                )
            )
    return result


def assert_error(response, code):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    parsed = events(response)
    assert parsed[-1][0] == "error"
    error = json.loads(parsed[-1][1])["error"]
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    assert "[DONE]" not in response.text
    assert "SECRET" not in response.text


INVALID_PAYLOADS = [
    {},
    {"messages": []},
    {"messages": "Hello"},
    {"messages": [{"role": "user"}]},
    {"messages": [{"role": "user", "content": " \n "}]},
    {"messages": [{"role": "user", "content": 123}]},
    {"messages": [{"role": "tool", "content": "Hello"}]},
    {"messages": [{"role": "assistant", "content": "Hello"}]},
    {"messages": [{"role": "system", "content": "Hello"}]},
    {"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]},
    {"messages": [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]},
    {"messages": [{"role": "user", "content": "a"}, {"role": "system", "content": "b"}]},
    {"messages": [{"role": "user", "content": "x" * 4001}]},
    {
        "messages": [
            {"role": "user", "content": "x" * 3000},
            {"role": "assistant", "content": "x" * 3000},
            {"role": "user", "content": "x" * 3000},
        ]
    },
    {
        "messages": [
            {"role": "user" if i % 2 == 0 else "assistant", "content": "x"} for i in range(17)
        ]
    },
    {"messages": [{"role": "user", "content": "Hello", "name": "Tommy"}]},
    {**PROMPT, "model": "other-model"},
    {**PROMPT, "upstream_url": "http://example.com"},
    *({**PROMPT, "max_tokens": value} for value in (0, 2049, True, "8", 2.5, None)),
    *({**PROMPT, "temperature": value} for value in (-0.1, 2.1, True, "1", None)),
    *({**PROMPT, "top_p": value} for value in (0, -0.1, 1.1, True, "0.9", None)),
    *({**PROMPT, "seed": value} for value in (-1, 2147483648, True, "42", 0.5, None)),
]


@pytest.mark.parametrize("payload", INVALID_PAYLOADS)
def test_invalid_schema_never_contacts_upstream(payload):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=DONE)

    async def check():
        async with client_for(handler) as client:
            response = await client.post("/api/chat", json=payload)
        assert response.status_code == 422
        assert calls == []

    asyncio.run(check())


@pytest.mark.parametrize("field", ["temperature", "top_p"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_numbers_are_rejected_without_upstream(field, value):
    calls = []

    async def check():
        async with client_for(lambda request: calls.append(request)) as client:
            body = json.dumps(PROMPT)[:-1] + f', "{field}": {value}' + "}"
            response = await client.post(
                "/api/chat", content=body, headers={"content-type": "application/json"}
            )
        assert response.status_code == 422
        assert calls == []

    asyncio.run(check())


@pytest.mark.parametrize("custom", [False, True])
def test_request_payload_is_bounded_and_fixed_to_local_model(custom):
    seen = []
    stream = TrackedStream([TEXT, DONE])

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async def check():
        payload = (
            PROMPT
            if not custom
            else {
                "messages": [
                    {"role": "system", "content": "Be concise"},
                    {"role": "user", "content": "a"},
                    {"role": "assistant", "content": "b"},
                    {"role": "user", "content": "c"},
                ],
                "max_tokens": 2048,
                "temperature": 2.0,
                "top_p": 1.0,
                "seed": 2147483647,
            }
        )
        async with client_for(handler) as client:
            response = await client.post("/api/chat", json=payload)
        assert response.status_code == 200
        assert events(response) == [("message", TEXT.decode().strip()[6:]), ("message", "[DONE]")]
        assert len(seen) == 1
        assert seen[0].url == "http://127.0.0.1:8081/v1/chat/completions"
        upstream = json.loads(seen[0].content)
        assert upstream["model"] == "llama-3.2-1b-instruct"
        assert upstream["stream"] is True
        assert upstream["stream_options"]["include_usage"] is True
        assert upstream["cache_prompt"] is False
        assert upstream["messages"] == payload["messages"]
        for key, default in (
            ("max_tokens", 1024),
            ("temperature", 0.7),
            ("top_p", 0.9),
            ("seed", 42),
        ):
            assert upstream[key] == payload.get(key, default)
        assert stream.closed

    asyncio.run(check())


@pytest.mark.parametrize("max_tokens", [1, 513, 1024, 2048])
def test_requested_output_token_limit_is_forwarded_without_clamping(max_tokens):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=DONE)

    async def check():
        async with client_for(handler) as client:
            response = await client.post("/api/chat", json={**PROMPT, "max_tokens": max_tokens})
        assert response.status_code == 200
        assert events(response)[-1] == ("message", "[DONE]")
        assert len(seen) == 1
        assert seen[0]["max_tokens"] == max_tokens

    asyncio.run(check())


def test_sse_accepts_fragmented_utf8_comments_crlf_multiline_role_and_usage():
    role = '{"choices":[{"delta":{"role":"assistant"}}]}'
    text = '{"choices":[\n{"delta":{"content":"Chào 👋"}}]}'
    usage = '{"choices":[],"usage":{"completion_tokens":2}}'
    wire = (
        ": keepalive\r\n\r\ndata: "
        + role
        + "\r\n\r\ndata: "
        + text.replace("\n", "\r\ndata: ")
        + "\r\n\r\ndata: "
        + usage
        + "\r\n\r\ndata: [DONE]\r\n\r\n"
    ).encode()
    stream = TrackedStream([bytes([byte]) for byte in wire])

    async def check():
        async with client_for(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )
        ) as client:
            response = await client.post("/api/chat", json=PROMPT)
        parsed = events(response)
        assert [json.loads(data) for _, data in parsed[:-1]] == [
            json.loads(role),
            json.loads(text),
            json.loads(usage),
        ]
        assert parsed[-1] == ("message", "[DONE]")
        assert stream.closed

    asyncio.run(check())


@pytest.mark.parametrize(
    "status,code",
    [
        (302, "upstream_error"),
        (400, "upstream_rejected"),
        (413, "upstream_rejected"),
        (422, "upstream_rejected"),
        (429, "upstream_unavailable"),
        (500, "upstream_error"),
        (503, "upstream_unavailable"),
    ],
)
def test_upstream_status_becomes_safe_stream_error_without_retry(status, code):
    stream = TrackedStream([b"SECRET upstream body"])
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, stream=stream)

    async def check():
        async with client_for(handler) as client:
            response = await client.post("/api/chat", json=PROMPT)
        assert_error(response, code)
        assert len(calls) == 1
        assert stream.closed

    asyncio.run(check())


@pytest.mark.parametrize(
    "error,code",
    [
        (httpx.ConnectError("SECRET host"), "upstream_unavailable"),
        (httpx.ConnectTimeout("SECRET host"), "upstream_timeout"),
        (httpx.ReadTimeout("SECRET data"), "upstream_timeout"),
        (httpx.RemoteProtocolError("SECRET wire"), "upstream_error"),
    ],
)
def test_upstream_transport_error_is_sanitized(error, code):
    async def handler(_):
        raise error

    async def check():
        async with client_for(handler) as client:
            response = await client.post("/api/chat", json=PROMPT)
        assert_error(response, code)

    asyncio.run(check())


@pytest.mark.parametrize(
    "chunks",
    [
        [TEXT],
        [b"data: {bad SECRET json}\n\n"],
        [b'data: {"x":NaN}\n\n'],
        [b'data: {"x":"' + b"x" * 65537 + b'"}\n\n'],
        [b'data: {"x":"' + b"x" * 65537],
        [b"data: \xff\n\n"],
    ],
)
def test_bad_sse_or_missing_done_closes_response(chunks):
    stream = TrackedStream(chunks)

    async def check():
        async with client_for(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )
        ) as client:
            response = await client.post("/api/chat", json=PROMPT)
        assert_error(response, "upstream_protocol_error")
        assert stream.closed

    asyncio.run(check())


def test_read_error_preserves_partial_output_and_closes_response():
    stream = TrackedStream([TEXT], error=httpx.ReadTimeout("SECRET stalled"))

    async def check():
        async with client_for(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )
        ) as client:
            response = await client.post("/api/chat", json=PROMPT)
        assert events(response)[0][1] == TEXT.decode().strip()[6:]
        assert_error(response, "upstream_timeout")
        assert stream.closed

    asyncio.run(check())


@pytest.mark.parametrize("waiting_for_headers", [False, True])
def test_total_deadline_bounds_header_wait_and_continuous_progress(waiting_for_headers):
    stream = TrackedStream([TEXT] * 100, delay=0.01)
    cancelled = []

    async def handler(_):
        if waiting_for_headers:
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(True)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async def check():
        settings = Settings(connect_timeout=1, read_timeout=1, total_timeout=0.06)
        async with client_for(handler, settings) as client:
            response = await asyncio.wait_for(client.post("/api/chat", json=PROMPT), timeout=1)
        assert_error(response, "upstream_timeout")
        assert cancelled if waiting_for_headers else stream.closed
        if not waiting_for_headers:
            assert '"content":"Hello"' in response.text

    asyncio.run(check())


def test_shared_upstream_transport_lives_until_application_shutdown():
    transport = TrackedTransport(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=DONE)
    )
    app = create_app(transport=transport)

    async def check():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                assert (await client.get("/health")).json() == {"status": "ok"}
                for _ in range(2):
                    assert (await client.post("/api/chat", json=PROMPT)).status_code == 200
                    assert not transport.closed
        assert transport.closed

    asyncio.run(check())


async def call_asgi(app, *, spec_version, disconnect, send):
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": json.dumps(PROMPT).encode(), "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat",
        "raw_path": b"/api/chat",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 8000),
    }
    await app(scope, receive, send)


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
@pytest.mark.parametrize("before_headers", [False, True])
def test_disconnect_cancels_pending_upstream_and_closes_response(spec_version, before_headers):
    async def check():
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        disconnected = asyncio.Event()
        chunks_sent = []

        class PendingStream(TrackedStream):
            async def __aiter__(self):
                yield TEXT
                entered.set()
                try:
                    await asyncio.sleep(10)
                finally:
                    cancelled.set()

        stream = PendingStream()

        async def handler(_):
            if before_headers:
                entered.set()
                try:
                    await asyncio.sleep(10)
                finally:
                    cancelled.set()
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

        async def send(message):
            if disconnected.is_set() and spec_version == "2.4":
                raise OSError("Client disconnected")
            if message["type"] == "http.response.body":
                chunks_sent.append(message.get("body", b""))

        app = create_app(transport=httpx.MockTransport(handler))
        async with app.router.lifespan_context(app):
            task = asyncio.create_task(
                call_asgi(app, spec_version=spec_version, disconnect=disconnected, send=send)
            )
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
                if not before_headers:
                    assert any(b'"content":"Hello"' in chunk for chunk in chunks_sent)
                disconnected.set()
                await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
                assert cancelled.is_set()
                if not before_headers:
                    assert stream.closed
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(check())


def test_exact_message_content_and_generation_bounds_are_accepted():
    messages = [{"role": "system", "content": "s" * 500}] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 500} for i in range(15)
    ]
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=DONE)

    async def check():
        async with client_for(handler) as client:
            for history in (messages, [{"role": "user", "content": "x" * 4000}]):
                response = await client.post(
                    "/api/chat",
                    json={
                        "messages": history,
                        "max_tokens": 1,
                        "temperature": 0,
                        "top_p": 0.001,
                        "seed": 0,
                    },
                )
                assert response.status_code == 200
                assert events(response)[-1] == ("message", "[DONE]")
        assert len(seen) == 2
        assert seen[0]["messages"] == messages

    asyncio.run(check())


def test_upstream_error_event_cannot_leak_internal_message():
    stream = TrackedStream(
        [TEXT, b'data: {"error":{"message":"SECRET internal details"}}\n\n', DONE]
    )

    async def check():
        async with client_for(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )
        ) as client:
            response = await client.post("/api/chat", json=PROMPT)
        assert_error(response, "upstream_error")
        assert stream.closed

    asyncio.run(check())


def test_oversized_unterminated_line_is_rejected_without_waiting_for_eof():
    class OversizedStream(TrackedStream):
        async def __aiter__(self):
            yield b'data: {"content":"' + b"x" * 65537
            await asyncio.sleep(10)

    stream = OversizedStream()

    async def check():
        async with client_for(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )
        ) as client:
            response = await asyncio.wait_for(client.post("/api/chat", json=PROMPT), timeout=0.5)
        assert_error(response, "upstream_protocol_error")
        assert stream.closed

    asyncio.run(check())


@pytest.mark.parametrize("field", ["connect_timeout", "read_timeout", "total_timeout"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_settings_reject_invalid_timeouts(field, value):
    with pytest.raises(ValueError):
        Settings(**{field: value})


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:8081",
        "https://127.0.0.1:8081",
        "http://user:password@127.0.0.1:8081",
        "http://127.0.0.1:8081/path",
        "http://127.0.0.1:8081?url=example.com",
    ],
)
def test_settings_restrict_inference_to_loopback_origin(url):
    with pytest.raises(ValueError):
        Settings(upstream_url=url)


def test_configured_connect_and_idle_timeouts_reach_httpx():
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=DONE)

    async def check():
        async with client_for(
            handler, Settings(connect_timeout=0.12, read_timeout=0.34, total_timeout=0.56)
        ) as client:
            response = await client.post("/api/chat", json=PROMPT)
        assert events(response)[-1] == ("message", "[DONE]")
        assert seen[0]["connect"] == 0.12
        assert seen[0]["read"] == 0.34

    asyncio.run(check())


def test_event_character_bound_allows_multibyte_utf8_below_limit():
    value = {"choices": [{"delta": {"content": "👋" * 20000}}]}
    wire = ("data: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode()
    assert len(wire) > 65536
    stream = TrackedStream([wire, DONE])

    async def check():
        async with client_for(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )
        ) as client:
            response = await client.post("/api/chat", json=PROMPT)
        parsed = events(response)
        assert json.loads(parsed[0][1]) == value
        assert parsed[-1] == ("message", "[DONE]")
        assert stream.closed

    asyncio.run(check())


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
def test_server_cancellation_closes_active_upstream_response(spec_version):
    async def check():
        entered = asyncio.Event()

        class PendingStream(TrackedStream):
            async def __aiter__(self):
                yield TEXT
                entered.set()
                await asyncio.sleep(10)

        stream = PendingStream()
        app = create_app(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=stream
                )
            )
        )

        async def send(_):
            pass

        async with app.router.lifespan_context(app):
            task = asyncio.create_task(
                call_asgi(app, spec_version=spec_version, disconnect=asyncio.Event(), send=send)
            )
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
                task.cancel()
                result = await asyncio.wait_for(
                    asyncio.gather(task, return_exceptions=True), timeout=0.5
                )
                assert isinstance(result[0], asyncio.CancelledError)
                assert stream.closed
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(check())


def test_asgi24_send_failure_closes_upstream_without_receive_disconnect():
    stream = TrackedStream([TEXT, DONE])
    app = create_app(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )
        )
    )

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("closed socket")

    async def check():
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(
                call_asgi(app, spec_version="2.4", disconnect=asyncio.Event(), send=send),
                timeout=0.5,
            )
        assert stream.closed

    asyncio.run(check())
