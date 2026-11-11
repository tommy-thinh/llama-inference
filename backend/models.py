"""PostgreSQL persistence rows and database-enforced invariants."""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DailyQuota(Base):
    __tablename__ = "daily_quotas"
    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    subject_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    requests: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    __table_args__ = (
        CheckConstraint("scope IN ('ip','session')", name="ck_quota_scope"),
        CheckConstraint("requests >= 0 AND tokens >= 0", name="ck_quota_usage"),
    )


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(80), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    next_position: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    __table_args__ = (
        CheckConstraint("length(trim(title)) BETWEEN 1 AND 80", name="ck_conversation_title"),
        CheckConstraint("next_position > 0", name="ck_conversation_position"),
        Index("ix_conversations_owner_updated", "session_id", "updated_at", "id"),
    )


class InferenceRequest(Base):
    __tablename__ = "inference_requests"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(16), default="running")
    model: Mapped[str] = mapped_column(String(64), default="llama-3.2-1b-instruct")
    max_tokens: Mapped[int] = mapped_column(Integer)
    temperature: Mapped[float] = mapped_column(Float)
    top_p: Mapped[float] = mapped_column(Float)
    seed: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ttft_seconds: Mapped[float | None] = mapped_column(Float)
    latency_seconds: Mapped[float | None] = mapped_column(Float)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    decode_tokens_per_second: Mapped[float | None] = mapped_column(Float)
    error_code: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        CheckConstraint(
            "status IN ('running','completed','cancelled','failed')", name="ck_request_status"
        ),
        CheckConstraint("(status = 'running') = (finished_at IS NULL)", name="ck_request_finished"),
        CheckConstraint(
            "max_tokens BETWEEN 1 AND 2048 AND temperature BETWEEN 0 AND 2 "
            "AND top_p > 0 AND top_p <= 1 AND seed BETWEEN 0 AND 2147483647",
            name="ck_request_settings",
        ),
        CheckConstraint(
            "ttft_seconds >= 0 AND latency_seconds >= 0 AND prompt_tokens >= 0 "
            "AND completion_tokens >= 0 AND decode_tokens_per_second >= 0",
            name="ck_request_metrics",
        ),
        Index("ix_requests_conversation_started", "conversation_id", "started_at"),
        Index(
            "uq_request_running",
            "conversation_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("inference_requests.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("conversation_id", "position", name="uq_message_position"),
        UniqueConstraint("request_id", "role", name="uq_message_request_role"),
        CheckConstraint("position > 0", name="ck_message_position"),
        CheckConstraint("role IN ('user','assistant')", name="ck_message_role"),
        CheckConstraint(
            "status IN ('partial','completed','cancelled','failed')", name="ck_message_status"
        ),
    )
