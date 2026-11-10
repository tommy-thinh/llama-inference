"""Validated local application settings."""

import math
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    upstream_url: str = "http://127.0.0.1:8081"
    connect_timeout: float = 5
    read_timeout: float = 30
    total_timeout: float = 120
    database_url: str | None = field(default=None, repr=False)
    session_cookie_secure: bool = False
    public_origin: str | None = None
    ip_requests_per_minute: int = 60
    session_requests_per_minute: int = 6
    daily_session_requests: int = 50
    daily_session_tokens: int = 32768
    daily_ip_requests: int = 200
    daily_ip_tokens: int = 131072
    queue_size: int = 2
    queue_wait_timeout: float = 10

    def __post_init__(self) -> None:
        if self.public_origin:
            origin = urlsplit(self.public_origin)
            if (
                origin.scheme != "https"
                or not origin.hostname
                or origin.username
                or origin.password
                or origin.path
                or origin.query
                or origin.fragment
            ):
                raise ValueError("Public origin must be an HTTPS origin without a path")
            if not self.database_url or not self.session_cookie_secure:
                raise ValueError("Public mode requires a database and Secure session cookies")
        for name in (
            "ip_requests_per_minute",
            "session_requests_per_minute",
            "daily_session_requests",
            "daily_session_tokens",
            "daily_ip_requests",
            "daily_ip_tokens",
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 10000000:
                raise ValueError(f"Invalid positive limit: {name}")
        if type(self.queue_size) is not int or not 0 <= self.queue_size <= 10:
            raise ValueError("Queue size must be between zero and ten")
        if not math.isfinite(self.queue_wait_timeout) or not 0 < self.queue_wait_timeout <= 60:
            raise ValueError("Queue wait must be positive and at most 60 seconds")
        url = urlsplit(self.upstream_url)
        if (
            url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or (url.port is not None and not 1 <= url.port <= 65535)
        ):
            raise ValueError("Inference URL must be an HTTP loopback origin")
        for value in (self.connect_timeout, self.read_timeout, self.total_timeout):
            if not math.isfinite(value) or not 0 < value <= 300:
                raise ValueError("Timeouts must be positive and at most 300 seconds")
        if self.database_url:
            from sqlalchemy.engine import make_url

            database = make_url(self.database_url)
            if database.drivername != "postgresql+psycopg" or database.host not in {
                "127.0.0.1",
                "localhost",
                "::1",
            }:
                raise ValueError("Database URL must use psycopg and a loopback host")

    @classmethod
    def from_environment(cls) -> "Settings":
        secure = os.getenv("LLAMA_SESSION_COOKIE_SECURE", "false").lower()
        if secure not in {"true", "false"}:
            raise ValueError("LLAMA_SESSION_COOKIE_SECURE must be true or false")
        return cls(
            upstream_url=os.getenv("LLAMA_API_UPSTREAM_URL", cls.upstream_url),
            connect_timeout=float(os.getenv("LLAMA_API_CONNECT_TIMEOUT", cls.connect_timeout)),
            read_timeout=float(os.getenv("LLAMA_API_READ_TIMEOUT", cls.read_timeout)),
            total_timeout=float(os.getenv("LLAMA_API_TOTAL_TIMEOUT", cls.total_timeout)),
            database_url=os.getenv("LLAMA_DATABASE_URL") or None,
            session_cookie_secure=secure == "true",
            public_origin=os.getenv("LLAMA_PUBLIC_ORIGIN") or None,
            **{
                name: int(os.getenv("LLAMA_" + name.upper(), getattr(cls, name)))
                for name in (
                    "ip_requests_per_minute",
                    "session_requests_per_minute",
                    "daily_session_requests",
                    "daily_session_tokens",
                    "daily_ip_requests",
                    "daily_ip_tokens",
                    "queue_size",
                )
            },
            queue_wait_timeout=float(os.getenv("LLAMA_QUEUE_WAIT_TIMEOUT", cls.queue_wait_timeout)),
        )
