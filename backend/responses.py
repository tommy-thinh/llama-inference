"""Disconnect-aware bounded streaming response shared by chat routes."""

import anyio
from fastapi.responses import StreamingResponse


class ChatStreamingResponse(StreamingResponse):
    """Monitor disconnect even while waiting for the first upstream byte."""

    def __init__(self, content, *, total_timeout: float, **kwargs) -> None:
        super().__init__(content, **kwargs)
        self.total_timeout = total_timeout

    async def __call__(self, scope, receive, send) -> None:
        # Always keep a receive listener, including on ASGI 2.4 where the base
        # class detects disconnect only through a failed downstream send.
        try:
            async with anyio.create_task_group() as group:

                async def produce() -> None:
                    try:
                        # A short terminal-frame grace keeps every downstream send bounded.
                        with anyio.move_on_after(self.total_timeout + 0.1):
                            await self.stream_response(send)
                    except OSError:
                        pass  # Client socket closed during send.
                    finally:
                        with anyio.CancelScope(shield=True):
                            await self.body_iterator.aclose()
                        group.cancel_scope.cancel()

                group.start_soon(produce)
                await self.listen_for_disconnect(receive)
                group.cancel_scope.cancel()
        finally:
            if self.background is not None:
                with anyio.CancelScope(shield=True):
                    await self.background()
