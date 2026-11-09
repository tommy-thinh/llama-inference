"""FastAPI routes, local assets, and per-application HTTP client lifespan."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from backend.config import Settings
from backend.conversations import router
from backend.database import Database
from backend.inference import stream_chat
from backend.responses import ChatStreamingResponse
from backend.schemas import ChatRequest

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

    app = FastAPI(title="Local Llama chat", lifespan=lifespan)
    app.state.settings = selected
    app.include_router(router)

    @app.exception_handler(SQLAlchemyError)
    async def database_failure(request: Request, exc: SQLAlchemyError):
        return JSONResponse(
            status_code=503, content={"detail": "Persistence is temporarily unavailable"}
        )

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
