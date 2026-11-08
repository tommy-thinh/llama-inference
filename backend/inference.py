"""Bounded progressive SSE relay with explicit upstream response ownership."""

import asyncio
import json
from collections.abc import AsyncIterator

import anyio
import httpx

from backend.config import Settings


class UpstreamFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class UpstreamProtocolError(ValueError):
    pass


def error_event(code: str, message: str) -> bytes:
    data = json.dumps({"error": {"code": code, "message": message}})
    return f"event: error\ndata: {data}\n\n".encode()


def reject_json_constant(value: str) -> None:
    raise ValueError("Nonfinite JSON number")


async def relay_events(response: httpx.Response) -> AsyncIterator[bytes]:
    lines: list[str] = []
    data_lines: list[str] = []
    event_type = ""
    size = 0
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.removesuffix("\r")
            if line:
                size += len(line) + 1
                if size > 65536:
                    raise UpstreamProtocolError("Event exceeds limit")
                if line.startswith(":"):
                    continue
                lines.append(line)
                field, _, value = line.partition(":")
                if field == "data":
                    data_lines.append(value.removeprefix(" "))
                elif field == "event":
                    event_type = value.removeprefix(" ")
                continue
            data = "\n".join(data_lines)
            if data_lines:
                if data != "[DONE]":
                    try:
                        event = json.loads(data, parse_constant=reject_json_constant)
                    except (ValueError, RecursionError) as exc:
                        raise UpstreamProtocolError("Invalid event JSON") from exc
                    if not isinstance(event, dict):
                        raise UpstreamProtocolError("Expected an event object")
                    if "error" in event or event_type == "error":
                        raise UpstreamFailure(
                            "upstream_error", "The inference server reported an error."
                        )
                yield ("\n".join(lines) + "\n\n").encode()
                if data == "[DONE]":
                    return
            lines, data_lines, size, event_type = [], [], 0, ""
        if len(buffer) + size > 65536:
            raise UpstreamProtocolError("Event exceeds limit")
    raise UpstreamProtocolError("Stream ended before its terminator")


async def stream_chat(
    client: httpx.AsyncClient, payload: dict, settings: Settings
) -> AsyncIterator[bytes]:
    response = None
    failure = None
    try:
        async with asyncio.timeout(settings.total_timeout):
            request = client.build_request("POST", "/v1/chat/completions", json=payload)
            response = await client.send(request, stream=True)
            if response.status_code in {400, 413, 422}:
                raise UpstreamFailure(
                    "upstream_rejected",
                    "Inference rejected the prompt. Try a shorter conversation.",
                )
            if response.status_code in {429, 503}:
                raise UpstreamFailure(
                    "upstream_unavailable", "The inference server is busy or unavailable."
                )
            if not response.is_success:
                raise UpstreamFailure(
                    "upstream_error", "The inference server could not complete the request."
                )
            if (
                response.headers.get("content-type", "").split(";")[0].strip()
                != "text/event-stream"
            ):
                raise UpstreamProtocolError("Expected SSE")
            async for event in relay_events(response):
                yield event
    except UpstreamFailure as exc:
        failure = error_event(exc.code, str(exc))
    except (TimeoutError, httpx.TimeoutException):
        failure = error_event("upstream_timeout", "Generation timed out. Please try again.")
    except httpx.ConnectError:
        failure = error_event(
            "upstream_unavailable", "Cannot connect to the local inference server."
        )
    except UpstreamProtocolError:
        failure = error_event(
            "upstream_protocol_error", "The inference stream was incomplete or invalid."
        )
    except httpx.HTTPError:
        failure = error_event(
            "upstream_error", "The connection to the inference server was interrupted."
        )
    finally:
        if response is not None:
            # Disconnect cancels the producer; closure must survive that cancellation.
            with anyio.CancelScope(shield=True):
                await response.aclose()
    if failure is not None:
        # Release the model slot before a slow downstream can block error delivery.
        yield failure
