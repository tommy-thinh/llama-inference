"""FastAPI routes, local assets, and per-application HTTP client lifespan."""

from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.config import Settings
from backend.inference import stream_chat
from backend.schemas import ChatRequest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


class ChatStreamingResponse(StreamingResponse):
    """Monitor disconnect even while waiting for the first upstream byte."""

    def __init__(self, content, *, total_timeout: float, **kwargs) -> None:
        super().__init__(content, **kwargs)
        self.total_timeout = total_timeout

    async def __call__(self, scope, receive, send) -> None:
        # Always keep a receive listener, including on ASGI 2.4 where the base
        # class detects disconnect only through a failed downstream send.
        async with anyio.create_task_group() as group:

            async def produce() -> None:
                try:
                    # A short terminal-frame grace keeps every downstream send bounded.
                    with anyio.move_on_after(self.total_timeout + 0.1):
                        await self.stream_response(send)
                except OSError:
                    pass  # Client socket closed during send.
                finally:
                    with anyio.CancelScope(shield=True):
                        await self.body_iterator.aclose()
                    group.cancel_scope.cancel()

            group.start_soon(produce)
            await self.listen_for_disconnect(receive)
            group.cancel_scope.cancel()
        if self.background is not None:
            await self.background()


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    selected = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(
            base_url=selected.upstream_url,
            timeout=httpx.Timeout(
                selected.read_timeout,
                connect=selected.connect_timeout,
                pool=selected.connect_timeout,
            ),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
            trust_env=False,
            transport=transport,
        ) as client:
            app.state.client = client
            yield

    app = FastAPI(title="Local Llama chat", lifespan=lifespan)
    app.state.settings = selected
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Echoing invalid NaN/Infinity inputs would itself break JSON encoding.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()
                ]
            },
        )

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(FRONTEND / "index.html")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/api/chat", response_class=StreamingResponse)
    async def chat(payload: ChatRequest, request: Request):
        return ChatStreamingResponse(
            stream_chat(request.app.state.client, payload.upstream_payload(), selected),
            total_timeout=selected.total_timeout,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()
