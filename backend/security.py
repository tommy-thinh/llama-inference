"""ASGI trust boundaries, bounded bodies, headers, and privacy-preserving logs."""

import asyncio
import ipaddress
import json
import logging
import time
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

LOGGER = logging.getLogger("llama.requests")
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class SecurityMiddleware:
    def __init__(self, app, settings, limiter):
        self.app, self.settings, self.limiter = app, settings, limiter

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        identity = uuid4().hex
        scope.setdefault("state", {})["request_id"] = identity
        status, started = 500, time.monotonic()
        settings = self.settings
        headers = Headers(scope=scope)

        async def safe_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                values = dict(SECURITY_HEADERS, **{"X-Request-ID": identity})
                if settings.public_origin:
                    values["Strict-Transport-Security"] = "max-age=31536000"
                existing = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in {name.lower().encode() for name in values}
                ]
                message["headers"] = existing + [
                    (k.lower().encode(), v.encode()) for k, v in values.items()
                ]
            await send(message)

        try:
            peer = (scope.get("client") or ("127.0.0.1", 0))[0]
            try:
                client_ip = ipaddress.ip_address(peer)
            except ValueError:
                raise HTTPException(400, "Invalid client address") from None
            if settings.public_origin:
                if (
                    headers.get("host", "").lower()
                    != urlsplit(settings.public_origin).netloc.lower()
                ):
                    raise HTTPException(400, "Unrecognized host")
                forwarded = headers.getlist("x-real-ip")
                if client_ip.is_loopback and forwarded:
                    if len(forwarded) != 1:
                        raise HTTPException(400, "Invalid client address")
                    try:
                        client_ip = ipaddress.ip_address(forwarded[0])
                    except ValueError:
                        raise HTTPException(400, "Invalid client address") from None
                if getattr(client_ip, "ipv4_mapped", None):
                    client_ip = client_ip.ipv4_mapped
                if scope["path"].startswith("/api/"):
                    self.limiter.check("ip", str(client_ip), settings.ip_requests_per_minute)
                    if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
                        if (
                            headers.get("origin") != settings.public_origin
                            or headers.get("sec-fetch-site") == "cross-site"
                        ):
                            raise HTTPException(403, "Same-origin requests required")
                        if headers.get("content-type", "").split(";")[
                            0
                        ].lower().strip() != "application/json" and (
                            headers.get("content-length", "0") != "0"
                            or headers.get("transfer-encoding")
                        ):
                            raise HTTPException(415, "JSON content type required")
            if getattr(client_ip, "ipv4_mapped", None):
                client_ip = client_ip.ipv4_mapped
            scope["state"]["client_ip"] = str(client_ip)
            body = bytearray()
            if scope["path"].startswith("/api/") and scope["method"] not in {
                "GET",
                "HEAD",
                "OPTIONS",
            }:
                try:
                    length = int(headers.get("content-length", "0"))
                except ValueError:
                    raise HTTPException(400, "Invalid content length") from None
                if length < 0:
                    raise HTTPException(400, "Invalid content length")
                if length > 65536:
                    raise HTTPException(413, "Request body too large")
                try:
                    async with asyncio.timeout(5):
                        while True:
                            part = await receive()
                            if part["type"] == "http.disconnect":
                                status = 499
                                return
                            body.extend(part.get("body", b""))
                            if len(body) > 65536:
                                raise HTTPException(413, "Request body too large")
                            if not part.get("more_body", False):
                                break
                except TimeoutError:
                    raise HTTPException(408, "Request body timed out") from None
                if (
                    settings.public_origin
                    and body
                    and headers.get("content-type", "").split(";")[0].lower().strip()
                    != "application/json"
                ):
                    raise HTTPException(415, "JSON content type required")
                delivered = False
                original_receive = receive

                async def buffered_receive():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await original_receive()

                receive = buffered_receive
            await self.app(scope, receive, safe_send)
        except HTTPException as exc:
            await JSONResponse(
                {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
            )(scope, receive, safe_send)
        finally:
            route = scope.get("route")
            LOGGER.info(
                json.dumps(
                    {
                        "request_id": identity,
                        "method": scope["method"],
                        "route": getattr(route, "path", "unmatched"),
                        "status": status,
                        "duration_seconds": round(time.monotonic() - started, 6),
                    }
                )
            )
