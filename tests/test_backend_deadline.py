"""Deadline/ownership regressions from docs/architecture.md, without sockets."""

import asyncio
import json

import httpx
import pytest

from backend.app import create_app
from backend.config import Settings

TEXT = b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
PROMPT = {"messages": [{"role": "user", "content": "Hello"}]}


class PendingStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


async def request_asgi(app, spec_version, send):
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": json.dumps(PROMPT).encode()}
        await asyncio.Event().wait()

    await app(
        {
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
        },
        receive,
        send,
    )


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
@pytest.mark.parametrize(
    "status,chunks,code",
    [
        pytest.param(200, [TEXT], "upstream_timeout", id="total-timeout"),
        pytest.param(400, [b"SECRET upstream body"], "upstream_rejected", id="rejected"),
        pytest.param(429, [b"SECRET upstream body"], "upstream_unavailable", id="busy"),
        pytest.param(500, [b"SECRET upstream body"], "upstream_error", id="server-error"),
        pytest.param(
            200,
            [TEXT, b'data: {"error":{"message":"SECRET internal details"}}\n\n'],
            "upstream_error",
            id="reported-error",
        ),
        pytest.param(
            200, [TEXT, b"data: SECRET invalid JSON\n\n"], "upstream_protocol_error", id="protocol"
        ),
    ],
)
def test_blocked_terminal_send_releases_upstream_and_finishes_within_deadline(
    spec_version, status, chunks, code
):
    """A browser that stalls only on errors must not hold the generation slot."""

    async def check():
        stream = PendingStream(chunks)
        attempted_errors = []
        closed_at_error = []
        sent = []

        async def send(message):
            body = message.get("body", b"")
            if body.startswith(b"event: error\n"):
                attempted_errors.append(body)
                closed_at_error.append(stream.closed)
                await asyncio.Event().wait()
            sent.append(body)

        app = create_app(
            settings=Settings(total_timeout=0.05),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    status, headers={"content-type": "text/event-stream"}, stream=stream
                )
            ),
        )
        async with app.router.lifespan_context(app):
            task = asyncio.create_task(request_asgi(app, spec_version, send))
            try:
                done, _ = await asyncio.wait({task}, timeout=0.4)
                assert attempted_errors, "Expected a terminal error before the bounded wait ended"
                body = attempted_errors[0]
                error = json.loads(body.split(b"data: ", 1)[1])["error"]
                assert error["code"] == code
                assert isinstance(error["message"], str) and error["message"]
                assert b"SECRET" not in body
                assert b"[DONE]" not in b"".join(sent) + body
                if status == 200:
                    assert TEXT in sent, "Partial text must reach the browser before failure"
                assert closed_at_error == [True], (
                    "Upstream must close before attempting a terminal error send; "
                    f"closed_at_error={closed_at_error}, request_finished={bool(done)}"
                )
                assert done, "A blocked terminal send outlived the 0.05 s request deadline"
                await task
                assert stream.closed
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(check())


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
def test_blocked_final_body_send_after_done_finishes_within_deadline(spec_version):
    async def check():
        stream = PendingStream([TEXT, b"data: [DONE]\n\n"])
        sent = []
        closed_at_final_send = []

        async def send(message):
            if message["type"] != "http.response.body":
                return
            if not message.get("more_body", False):
                closed_at_final_send.append(stream.closed)
                await asyncio.Event().wait()
            sent.append(message.get("body", b""))

        app = create_app(
            settings=Settings(total_timeout=0.05),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=stream
                )
            ),
        )
        async with app.router.lifespan_context(app):
            task = asyncio.create_task(request_asgi(app, spec_version, send))
            try:
                done, _ = await asyncio.wait({task}, timeout=0.4)
                assert sent == [TEXT, b"data: [DONE]\n\n"]
                assert closed_at_final_send == [True]
                assert done, "A blocked EOF send outlived the 0.05 s request deadline"
                await task
                assert stream.closed
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(check())
