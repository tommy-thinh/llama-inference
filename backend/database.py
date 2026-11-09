"""Short database transactions; no session or connection lives across generation."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.models import Conversation, InferenceRequest, Message, Session
from backend.schemas import ChatRequest

COOKIE_NAME = "llama_session"
SESSION_DAYS = 30


def now() -> datetime:
    return datetime.now(UTC)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def view(row, fields: str) -> dict:
    return {field: getattr(row, field) for field in fields.split()}


CONVERSATION_FIELDS = "id title created_at updated_at"
MESSAGE_FIELDS = "id request_id position role content status created_at updated_at"
REQUEST_FIELDS = (
    "id conversation_id status model max_tokens temperature top_p seed started_at finished_at "
    "ttft_seconds latency_seconds prompt_tokens completion_tokens "
    "decode_tokens_per_second error_code"
)


class Database:
    def __init__(self, url: str):
        self.engine = create_async_engine(
            url,
            pool_size=5,
            max_overflow=0,
            pool_timeout=5,
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={
                "connect_timeout": 5,
                "options": make_url(url).query.get("options", "") + " -c statement_timeout=5000",
            },
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def close(self) -> None:
        await self.engine.dispose()

    async def session_for_token(self, token: str | None) -> Session | None:
        if not token or not 40 <= len(token) <= 128:
            return None
        async with self.sessions.begin() as db:
            row = await db.scalar(
                select(Session).where(
                    Session.token_hash == token_hash(token), Session.expires_at > now()
                )
            )
            if row:
                row.last_seen_at = now()
            return row

    async def ensure_session(self, token: str | None) -> tuple[Session, str | None]:
        existing = await self.session_for_token(token)
        if existing:
            return existing, None
        raw = secrets.token_urlsafe(32)
        async with self.sessions.begin() as db:
            row = Session(
                token_hash=token_hash(raw), expires_at=now() + timedelta(days=SESSION_DAYS)
            )
            db.add(row)
            await db.flush()
        return row, raw

    async def _owned(self, db, owner: UUID, identity: UUID, *, lock=False) -> Conversation:
        query = select(Conversation).where(
            Conversation.id == identity, Conversation.session_id == owner
        )
        if lock:
            query = query.with_for_update()
        row = await db.scalar(query)
        if row is None:
            raise HTTPException(404, "Conversation not found")
        return row

    async def create_conversation(self, owner: UUID, title: str) -> dict:
        async with self.sessions.begin() as db:
            row = Conversation(session_id=owner, title=title)
            db.add(row)
            await db.flush()
        return view(row, CONVERSATION_FIELDS)

    async def list_conversations(self, owner: UUID, limit: int, offset: int) -> dict:
        async with self.sessions() as db:
            rows = (
                await db.scalars(
                    select(Conversation)
                    .where(Conversation.session_id == owner)
                    .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            return {
                "items": [view(row, CONVERSATION_FIELDS) for row in rows],
                "limit": limit,
                "offset": offset,
            }

    async def conversation(
        self, owner: UUID, identity: UUID, limit: int, before: int | None
    ) -> dict:
        async with self.sessions() as db:
            row = await self._owned(db, owner, identity)
            query = select(Message).where(Message.conversation_id == identity)
            if before is not None:
                query = query.where(Message.position < before)
            messages = list(
                (await db.scalars(query.order_by(Message.position.desc()).limit(limit + 1))).all()
            )
            more = len(messages) > limit
            messages = list(reversed(messages[:limit]))
            active = await db.scalar(
                select(InferenceRequest.id).where(
                    InferenceRequest.conversation_id == identity,
                    InferenceRequest.status == "running",
                )
            )
            return {
                **view(row, CONVERSATION_FIELDS),
                "messages": [view(m, MESSAGE_FIELDS) for m in messages],
                "active_request": active,
                "next_before_position": messages[0].position if more else None,
            }

    async def rename(self, owner: UUID, identity: UUID, title: str) -> dict:
        async with self.sessions.begin() as db:
            row = await self._owned(db, owner, identity, lock=True)
            row.title, row.updated_at = title, now()
        return view(row, CONVERSATION_FIELDS)

    async def delete_conversation(self, owner: UUID, identity: UUID) -> None:
        async with self.sessions.begin() as db:
            row = await self._owned(db, owner, identity, lock=True)
            if await db.scalar(
                select(InferenceRequest.id).where(
                    InferenceRequest.conversation_id == identity,
                    InferenceRequest.status == "running",
                )
            ):
                raise HTTPException(409, "Generation is still running")
            await db.delete(row)

    async def requests(self, owner: UUID, identity: UUID, limit: int, offset: int) -> dict:
        async with self.sessions() as db:
            await self._owned(db, owner, identity)
            rows = (
                await db.scalars(
                    select(InferenceRequest)
                    .where(InferenceRequest.conversation_id == identity)
                    .order_by(InferenceRequest.started_at.desc(), InferenceRequest.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            return {
                "items": [view(row, REQUEST_FIELDS) for row in rows],
                "limit": limit,
                "offset": offset,
            }

    async def reserve(self, owner: UUID, identity: UUID, turn) -> tuple[UUID, dict]:
        async with self.sessions.begin() as db:
            conversation = await self._owned(db, owner, identity, lock=True)
            if await db.scalar(
                select(InferenceRequest.id).where(
                    InferenceRequest.conversation_id == identity,
                    InferenceRequest.status == "running",
                )
            ):
                raise HTTPException(409, "Previous generation is still finishing")
            history = (
                await db.scalars(
                    select(Message)
                    .join(InferenceRequest, Message.request_id == InferenceRequest.id)
                    .where(
                        Message.conversation_id == identity,
                        InferenceRequest.status == "completed",
                        Message.status == "completed",
                    )
                    .order_by(Message.position)
                    .limit(17)
                )
            ).all()
            messages = [{"role": m.role, "content": m.content} for m in history]
            messages.append({"role": "user", "content": turn.content})
            try:
                payload = ChatRequest(
                    messages=messages, **turn.model_dump(exclude={"content"})
                ).upstream_payload()
            except ValueError as exc:
                raise HTTPException(
                    422, "Saved conversation exceeds context input limits; start a new conversation"
                ) from exc
            request = InferenceRequest(
                conversation_id=identity, **turn.model_dump(exclude={"content"})
            )
            db.add(request)
            await db.flush()
            db.add_all(
                [
                    Message(
                        conversation_id=identity,
                        request_id=request.id,
                        position=conversation.next_position,
                        role="user",
                        content=turn.content,
                        status="completed",
                    ),
                    Message(
                        conversation_id=identity,
                        request_id=request.id,
                        position=conversation.next_position + 1,
                        role="assistant",
                        content="",
                        status="partial",
                    ),
                ]
            )
            conversation.next_position += 2
            conversation.updated_at = now()
            if conversation.title == "New conversation":
                conversation.title = turn.content.strip()[:80]
            await db.flush()
        return request.id, payload

    async def checkpoint(self, identity: UUID, content: str, metrics: dict) -> None:
        async with self.sessions.begin() as db:
            request = await db.scalar(
                select(InferenceRequest).where(InferenceRequest.id == identity).with_for_update()
            )
            if request is None or request.status != "running":
                return
            for key, value in metrics.items():
                setattr(request, key, value)
            await db.execute(
                update(Message)
                .where(Message.request_id == identity, Message.role == "assistant")
                .values(content=content, updated_at=now())
            )

    async def finish(
        self,
        identity: UUID,
        status: str,
        content: str,
        metrics: dict,
        error_code: str | None = None,
    ) -> None:
        async with self.sessions.begin() as db:
            request = await db.scalar(
                select(InferenceRequest).where(InferenceRequest.id == identity).with_for_update()
            )
            if request is None or request.status != "running":
                raise RuntimeError("Request is no longer active")
            request.status, request.finished_at, request.error_code = status, now(), error_code
            for key, value in metrics.items():
                setattr(request, key, value)
            await db.execute(
                update(Message)
                .where(Message.request_id == identity, Message.role == "assistant")
                .values(content=content, status=status, updated_at=now())
            )
            await db.execute(
                update(Conversation)
                .where(Conversation.id == request.conversation_id)
                .values(updated_at=now())
            )

    async def recover(self) -> int:
        async with self.sessions.begin() as db:
            identities = list(
                (
                    await db.scalars(
                        select(InferenceRequest.id)
                        .where(InferenceRequest.status == "running")
                        .with_for_update()
                    )
                ).all()
            )
            if identities:
                await db.execute(
                    update(InferenceRequest)
                    .where(InferenceRequest.id.in_(identities))
                    .values(status="failed", finished_at=now(), error_code="backend_restarted")
                )
                await db.execute(
                    update(Message)
                    .where(Message.request_id.in_(identities), Message.role == "assistant")
                    .values(status="failed", updated_at=now())
                )
            return len(identities)

    async def cancel_unfinished(self, identity: UUID) -> None:
        async with self.sessions.begin() as db:
            request = await db.scalar(
                select(InferenceRequest).where(InferenceRequest.id == identity).with_for_update()
            )
            if request is None or request.status != "running":
                return
            request.status, request.finished_at = "cancelled", now()
            request.error_code = "client_disconnected"
            request.latency_seconds = max(0.0, (now() - request.started_at).total_seconds())
            await db.execute(
                update(Message)
                .where(Message.request_id == identity, Message.role == "assistant")
                .values(status="cancelled", updated_at=now())
            )

    async def retain(self, days: int = 30, apply: bool = False) -> dict:
        if days < 1:
            raise ValueError("Retention days must be positive")
        cutoff = now() - timedelta(days=days)
        async with self.sessions.begin() as db:
            conversations = []
            candidates = list(
                (
                    await db.scalars(
                        select(Conversation)
                        .where(Conversation.updated_at < cutoff)
                        .with_for_update()
                    )
                ).all()
            )
            for row in candidates:
                active = await db.scalar(
                    select(InferenceRequest.id).where(
                        InferenceRequest.conversation_id == row.id,
                        InferenceRequest.status == "running",
                    )
                )
                if active is None:
                    conversations.append(row.id)
            sessions = []
            expired = list(
                (
                    await db.scalars(
                        select(Session).where(Session.expires_at < now()).with_for_update()
                    )
                ).all()
            )
            for row in expired:
                owned = list(
                    (
                        await db.scalars(
                            select(Conversation.id)
                            .where(Conversation.session_id == row.id)
                            .with_for_update()
                        )
                    ).all()
                )
                active = await db.scalar(
                    select(InferenceRequest.id).where(
                        InferenceRequest.conversation_id.in_(owned),
                        InferenceRequest.status == "running",
                    )
                )
                if active is None:
                    sessions.append(row.id)
            if apply:
                await db.execute(delete(Conversation).where(Conversation.id.in_(conversations)))
                await db.execute(delete(Session).where(Session.id.in_(sessions)))
            return {
                "conversations": len(conversations),
                "sessions": len(sessions),
                "applied": apply,
            }
