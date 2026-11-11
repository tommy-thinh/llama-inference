"""Durable stream state without holding a database transaction across inference."""

import asyncio
import json
import math
import time

import anyio
from sqlalchemy.exc import SQLAlchemyError

from backend.inference import error_event, stream_chat


class PersistenceTimeout(TimeoutError):
    """A terminal database write exceeded its cleanup budget."""


class Finalization:
    """Share one five-second budget across terminal writes and disconnect fallback."""

    def __init__(self, database, identity):
        self.database = database
        self.identity = identity
        self.deadline = None
        self.outcome = "running"

    async def write(self, operation, *args):
        if self.deadline is None:
            self.deadline = time.monotonic() + 5
        with anyio.CancelScope(shield=True):
            try:
                async with asyncio.timeout(max(0, self.deadline - time.monotonic())):
                    return await operation(self.identity, *args)
            except TimeoutError as exc:
                raise PersistenceTimeout from exc

    async def finish(self, status, content, metrics, code=None):
        await self.write(self.database.finish, status, content, metrics, code)
        self.outcome = status

    async def cancel_unfinished(self):
        try:
            outcome = await self.write(self.database.cancel_unfinished)
            if outcome:
                self.outcome = outcome
        except (SQLAlchemyError, PersistenceTimeout):
            pass  # Recovery resolves the last durable checkpoint on restart.


async def persistent_events(
    database,
    identity,
    payload,
    client,
    settings,
    conversation_id,
    finalization,
    queue_wait_seconds=0.0,
):
    output = ""
    metrics = {}
    started = time.monotonic()
    deadline = started + settings.total_timeout
    checkpoint_at = 0.0
    terminal = False
    failure_code = None
    terminal_error = None
    upstream = stream_chat(client, payload, settings)

    async def finish(status, code=None):
        nonlocal terminal
        metrics["latency_seconds"] = max(0.0, time.monotonic() - started)
        await finalization.finish(status, output, metrics, code)
        terminal = True

    try:
        yield (
            "event: metadata\ndata: "
            + json.dumps(
                {
                    "request_id": str(identity),
                    "conversation_id": str(conversation_id),
                    "queue_wait_seconds": queue_wait_seconds,
                }
            )
            + "\n\n"
        ).encode()
        async for frame in upstream:
            data = "\n".join(
                line[5:].removeprefix(" ")
                for line in frame.decode().splitlines()
                if line.startswith("data:")
            )
            if data == "[DONE]":
                with anyio.CancelScope(shield=True):
                    await upstream.aclose()
                if not output:
                    failure_code = "upstream_protocol_error"
                    await finish("failed", failure_code)
                    yield error_event(failure_code, "The model returned no text.")
                else:
                    await finish("completed")
                    yield frame
                return
            packet = json.loads(data)
            if "error" in packet:
                failure_code = packet["error"].get("code", "upstream_error")
                await finish("failed", failure_code)
                yield frame
                return
            choices = packet.get("choices", [])
            for choice in choices:
                delta = choice.get("delta", {}).get("content")
                if delta is not None and not isinstance(delta, str):
                    raise ValueError("Invalid visible text")
                if delta:
                    metrics.setdefault("ttft_seconds", max(0.0, time.monotonic() - started))
                    output += delta
                    if len(output) > 65536:
                        raise ValueError("Output exceeds limit")
            usage = packet.get("usage") or {}
            for name in ("prompt_tokens", "completion_tokens"):
                if name in usage:
                    value = usage[name]
                    if type(value) is not int or value < 0:
                        raise ValueError("Invalid token count")
                    metrics[name] = value
            speed = (packet.get("timings") or {}).get("predicted_per_second")
            if speed is not None:
                if (
                    isinstance(speed, bool)
                    or not isinstance(speed, (int, float))
                    or not math.isfinite(speed)
                    or speed < 0
                ):
                    raise ValueError("Invalid runtime speed")
                metrics["decode_tokens_per_second"] = speed
            if output and time.monotonic() - checkpoint_at >= 0.25:
                async with asyncio.timeout(min(5, max(0, deadline - time.monotonic()))):
                    await database.checkpoint(identity, output, metrics)
                checkpoint_at = time.monotonic()
            yield frame
        raise ValueError("Stream ended without terminal event")
    except PersistenceTimeout:
        failure_code = "persistence_error"
        terminal_error = error_event(
            failure_code, "Could not save generation state. Please reload before retrying."
        )
    except TimeoutError:
        failure_code = "upstream_timeout" if time.monotonic() >= deadline else "persistence_error"
        terminal_error = error_event(
            failure_code, "Generation or persistence timed out. Please reload before retrying."
        )
    except SQLAlchemyError:
        failure_code = "persistence_error"
        terminal_error = error_event(
            failure_code, "Could not save generation state. Please reload before retrying."
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        failure_code = "upstream_protocol_error"
        try:
            await finish("failed", failure_code)
            terminal_error = error_event(
                failure_code, "The inference stream was incomplete or invalid."
            )
        except (SQLAlchemyError, TimeoutError):
            failure_code = "persistence_error"
            terminal_error = error_event(
                failure_code, "Could not save generation state. Please reload before retrying."
            )
    finally:
        with anyio.CancelScope(shield=True):
            await upstream.aclose()
            if not terminal:
                try:
                    await finalization.finish(
                        "failed" if failure_code else "cancelled",
                        output,
                        {**metrics, "latency_seconds": max(0.0, time.monotonic() - started)},
                        failure_code or "client_disconnected",
                    )
                except (SQLAlchemyError, TimeoutError, RuntimeError):
                    # Preserve the last durable checkpoint; startup recovery resolves running rows.
                    pass
    if terminal_error is not None:
        yield terminal_error
