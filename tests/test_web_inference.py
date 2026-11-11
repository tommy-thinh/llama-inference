"""Opt-in browser-facing API smoke test against the verified local Llama server."""

import asyncio
import json

import httpx
import pytest

from backend.app import create_app
from backend.config import Settings
from inference.server import ManagedServer

pytestmark = pytest.mark.inference


def test_local_web_api_streams_actual_llama_response(live_server: ManagedServer) -> None:
    async def check():
        app = create_app(settings=Settings(upstream_url=live_server.config.base_url))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                page = await client.get("/")
                assert page.status_code == 200
                assert 'id="chat-form"' in page.text
                response = await client.post(
                    "/api/chat",
                    json={
                        "messages": [
                            {
                                "role": "user",
                                "content": (
                                    "Count from 1 to 10, separated by commas, without explanation."
                                ),
                            }
                        ],
                        "max_tokens": 48,
                        "temperature": 0,
                    },
                )
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                assert "event: error" not in response.text
                text_chunks = []
                for line in response.text.splitlines():
                    if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                        continue
                    data = json.loads(line[5:])
                    for choice in data.get("choices", []):
                        content = choice.get("delta", {}).get("content", "")
                        if content:
                            text_chunks.append(content)
                assert len(text_chunks) > 1
                text = "".join(text_chunks)
                assert "1" in text and "10" in text
                assert response.text.rstrip().endswith("data: [DONE]")
                assert (await client.get("/health")).json() == {"status": "ok"}

    asyncio.run(check())
