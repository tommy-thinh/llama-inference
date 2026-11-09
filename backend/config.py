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

    def __post_init__(self) -> None:
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
        )
