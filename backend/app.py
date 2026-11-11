"""FastAPI routes, local assets, and per-application HTTP client lifespan."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from backend.config import Settings
from backend.conversations import router
from backend.database import Database
from backend.inference import stream_chat
from backend.resources import Admission, RateLimiter
from backend.responses import ChatStreamingResponse
from backend.schemas import ChatRequest
from backend.security import SecurityMiddleware

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


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
            app.state.database = Database(selected.database_url) if selected.database_url else None
            try:
                if app.state.database:
                    async with asyncio.timeout(5):
                        await app.state.database.recover()
                yield
            finally:
                if app.state.database:
                    await app.state.database.close()

    app = FastAPI(
        title="Llama chat",
        lifespan=lifespan,
        docs_url=None if selected.public_origin else "/docs",
        redoc_url=None if selected.public_origin else "/redoc",
        openapi_url=None if selected.public_origin else "/openapi.json",
    )
    app.state.settings = selected
    app.state.limiter = RateLimiter()
    app.state.admission = Admission(selected.queue_size, selected.queue_wait_timeout)
    app.add_middleware(SecurityMiddleware, settings=selected, limiter=app.state.limiter)
    app.include_router(router)

    @app.exception_handler(SQLAlchemyError)
    async def database_failure(request: Request, exc: SQLAlchemyError):
        return JSONResponse(
            status_code=503, content={"detail": "Persistence is temporarily unavailable"}
        )

    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
    app.mount("/legal", StaticFiles(directory=FRONTEND.parent / "legal"), name="legal")

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

    @app.get("/ready")
    async def ready(request: Request):
        try:
            async with asyncio.timeout(2):
                db = request.app.state.database
                if db is None or not await db.ping():
                    raise ValueError("Database unavailable")
                upstream = await request.app.state.client.get("/health")
                try:
                    if not upstream.is_success or upstream.json().get("status") != "ok":
                        raise ValueError("Inference unavailable")
                finally:
                    await upstream.aclose()
        except (SQLAlchemyError, httpx.HTTPError, TimeoutError, ValueError, AttributeError):
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return {"status": "ready"}

    @app.post("/api/chat", response_class=StreamingResponse)
    async def chat(payload: ChatRequest, request: Request):
        if selected.public_origin:
            raise HTTPException(404, "Not found")
        return ChatStreamingResponse(
            stream_chat(request.app.state.client, payload.upstream_payload(), selected),
            total_timeout=selected.total_timeout,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()
