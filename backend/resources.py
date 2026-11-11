"""Single-worker bounded rate windows and FIFO inference admission."""

import asyncio
import time
from collections import deque

import anyio
from fastapi import HTTPException


class RateLimiter:
    def __init__(self, capacity=4096):
        self.capacity = capacity
        self.windows = {}

    def check(self, scope, subject, limit):
        window = int(time.monotonic() // 60)
        key = (scope, subject)
        if key not in self.windows and len(self.windows) >= self.capacity:
            self.windows = {k: v for k, v in self.windows.items() if v[0] == window}
            if len(self.windows) >= self.capacity:
                raise HTTPException(503, "Request tracking is full", headers={"Retry-After": "60"})
        previous, count = self.windows.get(key, (window, 0))
        count = count if previous == window else 0
        if count >= limit:
            raise HTTPException(429, "Request rate limit exceeded", headers={"Retry-After": "60"})
        self.windows[key] = (window, count + 1)


class Permit:
    def __init__(self, admission):
        self.admission = admission
        self.released = False

    async def release(self):
        if not self.released:
            self.released = True
            self.admission.release()


class Admission:
    def __init__(self, queue_size=2, wait_timeout=10):
        self.queue_size = queue_size
        self.wait_timeout = wait_timeout
        self.active = False
        self.waiting = deque()

    async def acquire(self):
        if not self.active:
            self.active = True
            return Permit(self)
        if len(self.waiting) >= self.queue_size:
            raise HTTPException(503, "Inference queue is full", headers={"Retry-After": "10"})
        future = asyncio.get_running_loop().create_future()
        self.waiting.append(future)
        try:
            async with asyncio.timeout(self.wait_timeout):
                await future
        except BaseException as exc:
            if future.done() and not future.cancelled():
                self.release()  # Cancellation raced with a handed-off permit.
            elif future in self.waiting:
                self.waiting.remove(future)
            if isinstance(exc, TimeoutError):
                raise HTTPException(
                    503, "Inference queue wait expired", headers={"Retry-After": "10"}
                ) from None
            raise
        return Permit(self)

    def release(self):
        while self.waiting:
            future = self.waiting.popleft()
            if not future.done():
                future.set_result(True)
                return
        self.active = False

    async def for_request(self, request):
        async def disconnected():
            while True:
                if (await request.receive())["type"] == "http.disconnect":
                    return

        pending = asyncio.create_task(self.acquire())
        listener = asyncio.create_task(disconnected())
        granted = None
        try:
            done, _ = await asyncio.wait((pending, listener), return_when=asyncio.FIRST_COMPLETED)
            if listener in done:
                pending.cancel()
                try:
                    permit = await pending
                except (asyncio.CancelledError, HTTPException):
                    pass
                else:
                    await permit.release()
                raise asyncio.CancelledError
            granted = await pending
            return granted
        finally:
            with anyio.CancelScope(shield=True):
                listener.cancel()
                if not pending.done():
                    pending.cancel()
                result, _ = await asyncio.gather(pending, listener, return_exceptions=True)
                if granted is None and isinstance(result, Permit):
                    await result.release()
