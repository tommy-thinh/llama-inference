"""Cookie-owned persistent conversation endpoints."""

import asyncio
import json
import logging
import time
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from starlette.background import BackgroundTask

from backend.database import COOKIE_NAME, SESSION_DAYS
from backend.persistent_chat import Finalization, persistent_events
from backend.responses import ChatStreamingResponse
from backend.schemas import ConversationTitle, NewConversation, TurnRequest

router = APIRouter(prefix="/api")


def database(request: Request):
    selected = getattr(request.app.state, "database", None)
    if selected is None:
        raise HTTPException(503, "Persistence is not configured")
    return selected


async def owner(request: Request):
    session = await database(request).session_for_token(request.cookies.get(COOKIE_NAME))
    if session is None:
        raise HTTPException(401, "Session expired or missing; reload the page")
    return session.id


@router.post("/session")
async def session(request: Request, response: Response):
    row, token = await database(request).ensure_session(request.cookies.get(COOKIE_NAME))
    if token:
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=SESSION_DAYS * 86400,
            httponly=True,
            secure=request.app.state.settings.session_cookie_secure,
            samesite="lax",
            path="/api",
        )
    return {"status": "ok", "expires_at": row.expires_at}


@router.post("/conversations", status_code=201)
async def create(request: Request, body: NewConversation | None = None):
    title = body.title if body is not None else "New conversation"
    return await database(request).create_conversation(await owner(request), title)


@router.get("/conversations")
async def list_conversations(
    request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)
):
    return await database(request).list_conversations(await owner(request), limit, offset)


@router.get("/conversations/{identity}")
async def detail(
    request: Request,
    identity: UUID,
    limit: int = Query(100, ge=1, le=100),
    before_position: int | None = Query(None, ge=1),
):
    return await database(request).conversation(
        await owner(request), identity, limit, before_position
    )


@router.patch("/conversations/{identity}")
async def rename(request: Request, identity: UUID, body: ConversationTitle):
    return await database(request).rename(await owner(request), identity, body.title)


@router.delete("/conversations/{identity}", status_code=204)
async def remove(request: Request, identity: UUID):
    await database(request).delete_conversation(await owner(request), identity)


@router.get("/conversations/{identity}/requests")
async def requests(
    request: Request,
    identity: UUID,
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    return await database(request).requests(await owner(request), identity, limit, offset)


@router.post("/conversations/{identity}/chat")
async def chat(request: Request, identity: UUID, body: TurnRequest):
    db = database(request)
    settings = request.app.state.settings
    session_id = await owner(request)
    if settings.public_origin:
        request.app.state.limiter.check(
            "session", str(session_id), settings.session_requests_per_minute
        )
    request_id, payload = await db.reserve(
        session_id,
        identity,
        body,
        quota_ip=request.state.client_ip if settings.public_origin else None,
        limits=settings,
    )

    finalization = Finalization(db, request_id)
    permit = None
    admission_started = time.monotonic()
    try:
        if settings.public_origin:
            permit = await request.app.state.admission.for_request(request)
    except BaseException as exc:
        if isinstance(exc, asyncio.CancelledError):
            await finalization.cancel_unfinished()
        else:
            try:
                await finalization.finish("failed", "", {}, "admission_rejected")
            except Exception:
                pass  # Startup recovery preserves the reservation if persistence is unavailable.
        raise
    queue_wait_seconds = max(0.0, time.monotonic() - admission_started)

    async def cleanup():
        try:
            await finalization.cancel_unfinished()
        finally:
            if permit:
                await permit.release()
            logging.getLogger("llama.requests").info(
                json.dumps(
                    {
                        "event": "stream_complete",
                        "request_id": request.state.request_id,
                        "status": finalization.outcome,
                    }
                )
            )

    return ChatStreamingResponse(
        persistent_events(
            db,
            request_id,
            payload,
            request.app.state.client,
            request.app.state.settings,
            identity,
            finalization,
            queue_wait_seconds=queue_wait_seconds,
        ),
        # Headers can fail before the generator starts, so cleanup also belongs
        # to the response lifecycle. Closing an unstarted generator runs no finally.
        background=BackgroundTask(cleanup),
        total_timeout=request.app.state.settings.total_timeout,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Request-ID": str(request_id),
        },
    )
