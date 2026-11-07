"""SSE parsing and client measurements; token counts come from the serving runtime."""

import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from statistics import mean, median
from time import perf_counter
from typing import Any

import httpx


class StreamProtocolError(ValueError):
    """The upstream stream is incomplete or violates the expected chat contract."""


def iter_sse_json(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Parse SSE data events, ignoring comments; require the terminal [DONE] event."""
    data: list[str] = []
    for line in lines:
        if line.startswith("data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(" ") else value)
        elif line == "" and data:
            payload = "\n".join(data)
            data.clear()
            if payload == "[DONE]":
                return
            try:
                event = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise StreamProtocolError("Malformed JSON in SSE event") from exc
            if not isinstance(event, dict) or "error" in event:
                raise StreamProtocolError(f"Upstream error or non-object SSE event: {event}")
            yield event
    raise StreamProtocolError("SSE stream ended without [DONE]")


@dataclass(frozen=True)
class RequestMeasurement:
    output: str
    ttft_seconds: float
    total_seconds: float
    content_chunks: int
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str
    effective_tokens_per_second: float
    timings: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def stream_chat(client: httpx.Client, base_url: str, payload: dict[str, Any]) -> RequestMeasurement:
    start = perf_counter()
    ttft = None
    pieces: list[str] = []
    usage: dict[str, Any] = {}
    timings: dict[str, Any] = {}
    finish = None
    with client.stream("POST", base_url + "/v1/chat/completions", json=payload) as response:
        response.raise_for_status()
        if "text/event-stream" not in response.headers.get("content-type", ""):
            raise StreamProtocolError("Expected text/event-stream")
        for event in iter_sse_json(response.iter_lines()):
            if event.get("usage"):
                usage = event["usage"]
            if event.get("timings"):
                timings = event["timings"]
            for choice in event.get("choices", []):
                content = choice.get("delta", {}).get("content")
                if content:
                    if ttft is None:
                        ttft = perf_counter() - start
                    pieces.append(content)
                if choice.get("finish_reason") is not None:
                    finish = choice["finish_reason"]
    elapsed = perf_counter() - start
    tokens = usage.get("completion_tokens")
    prompt_tokens = usage.get("prompt_tokens")
    if ttft is None or not pieces or finish is None:
        raise StreamProtocolError("Stream must contain visible text and a finish reason")
    if not isinstance(tokens, int) or not 0 < tokens <= payload["max_tokens"]:
        raise StreamProtocolError("Missing or invalid completion token count")
    if not isinstance(prompt_tokens, int) or prompt_tokens <= 0:
        raise StreamProtocolError("Missing or invalid prompt token count")
    return RequestMeasurement(
        output="".join(pieces),
        ttft_seconds=ttft,
        total_seconds=elapsed,
        content_chunks=len(pieces),
        prompt_tokens=prompt_tokens,
        completion_tokens=tokens,
        finish_reason=finish,
        effective_tokens_per_second=tokens / elapsed,
        timings=timings,
    )


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize measured requests only, keeping different prompts separate."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("phase") == "measured" and "error" not in row:
            groups.setdefault((row["profile"], row["prompt_id"]), []).append(row)
    result = []
    for (profile, prompt_id), samples in groups.items():
        speeds = [
            s["timings"]["predicted_per_second"]
            for s in samples
            if "predicted_per_second" in s["timings"]
        ]
        result.append(
            {
                "profile": profile,
                "prompt_id": prompt_id,
                "requests": len(samples),
                "median_ttft_seconds": median(s["ttft_seconds"] for s in samples),
                "min_ttft_seconds": min(s["ttft_seconds"] for s in samples),
                "max_ttft_seconds": max(s["ttft_seconds"] for s in samples),
                "median_total_seconds": median(s["total_seconds"] for s in samples),
                "mean_runtime_decode_tokens_per_second": mean(speeds) if speeds else None,
                "mean_effective_tokens_per_second": mean(
                    s["effective_tokens_per_second"] for s in samples
                ),
                "mean_server_cpu_percent": mean(
                    s["server_cpu_percent"]
                    for s in samples
                    if s.get("server_cpu_percent") is not None
                )
                if any(s.get("server_cpu_percent") is not None for s in samples)
                else None,
                "completion_tokens": [s["completion_tokens"] for s in samples],
                "length_limited_requests": sum(s["finish_reason"] == "length" for s in samples),
                "peak_server_rss_bytes": max(s["memory"].get("peak_rss_bytes", 0) for s in samples),
            }
        )
    return result
