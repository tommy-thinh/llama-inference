import json
import math
from pathlib import Path

import httpx
import pytest

from benchmarks.scripts import metrics
from benchmarks.scripts.metrics import StreamProtocolError, iter_sse_json, stream_chat, summarize
from benchmarks.scripts.run import read_config, request_payload
from inference.artifacts import ROOT


def event(value: object) -> str:
    return "data: " + json.dumps(value, ensure_ascii=False) + "\n\n"


def stream_fixture() -> str:
    return (
        ": heartbeat\n\n"
        + event({"choices": [{"delta": {"role": "assistant"}}]})
        + event({"choices": [{"delta": {"content": "Hello "}}]})
        + event({"choices": [{"delta": {"content": "Tommy"}}]})
        + event(
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                "timings": {"predicted_per_second": 6.0, "cache_n": 0},
            }
        )
        + "data: [DONE]\n\n"
    )


def test_sse_supports_comments_and_multiline_data() -> None:
    stream = ': ping\n\ndata: {"choices":\ndata: []}\n\ndata: [DONE]\n\n'
    assert list(iter_sse_json(stream.splitlines())) == [{"choices": []}]


@pytest.mark.parametrize(
    "stream,match",
    [
        (event({"choices": []}), "without"),
        ("data: invalid\n\n", "Malformed"),
        (event({"error": {"message": "out of memory"}}), "Upstream error"),
        (event([]), "non-object"),
    ],
)
def test_incomplete_or_error_stream_is_a_failure(stream: str, match: str) -> None:
    with pytest.raises(StreamProtocolError, match=match):
        list(iter_sse_json(stream.splitlines()))


def test_ttft_ignores_role_event_and_uses_runtime_token_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [10.0]
    monkeypatch.setattr(metrics, "perf_counter", lambda: clock[0])

    class TimedStream(httpx.SyncByteStream):
        def __iter__(self):
            for at, body in (
                (10.1, ": heartbeat\n\n"),
                (10.2, event({"choices": [{"delta": {"role": "assistant"}}]})),
                (10.3, event({"choices": [{"delta": {"content": ""}}]})),
                (10.5, event({"choices": [{"delta": {"content": "Hello "}}]})),
                (10.8, event({"choices": [{"delta": {"content": "Tommy"}}]})),
                (
                    11.0,
                    event(
                        {
                            "choices": [{"delta": {}, "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                            "timings": {"predicted_per_second": 6.0},
                        }
                    ),
                ),
                (11.1, "data: [DONE]\n\n"),
            ):
                clock[0] = at
                yield body.encode()

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=TimedStream()
        )
    )
    with httpx.Client(transport=transport) as client:
        result = stream_chat(client, "http://test", request_payload("Hello", 16))
    assert result.output == "Hello Tommy"
    assert result.ttft_seconds == pytest.approx(0.5)
    assert result.total_seconds == pytest.approx(1.1)
    assert result.content_chunks == 2
    assert result.completion_tokens == 3
    assert result.effective_tokens_per_second == pytest.approx(3 / 1.1)
    assert result.timings["predicted_per_second"] == 6


@pytest.mark.parametrize("status", [400, 503])
def test_http_failure_is_not_a_benchmark_success(status: int) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status))) as client:
        with pytest.raises(httpx.HTTPStatusError):
            stream_chat(client, "http://test", request_payload("Hello", 16))


def test_missing_usage_is_rejected() -> None:
    stream = event({"choices": [{"delta": {"content": "4"}, "finish_reason": "stop"}]})
    stream += "data: [DONE]\n\n"
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream)
    )
    with (
        httpx.Client(transport=transport) as client,
        pytest.raises(StreamProtocolError, match="count"),
    ):
        stream_chat(client, "http://test", request_payload("Hello", 16))


def test_read_timeout_propagates() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("generation stalled", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(httpx.ReadTimeout):
            stream_chat(client, "http://test", request_payload("Hello", 16))


@pytest.mark.parametrize("failure", [httpx.ReadError, httpx.ReadTimeout])
def test_midstream_disconnect_or_timeout_closes_the_response(
    failure: type[httpx.HTTPError],
) -> None:
    class InterruptedStream(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            yield event({"choices": [{"delta": {"content": "partial answer"}}]}).encode()
            raise failure("generation connection interrupted")

        def close(self) -> None:
            self.closed = True

    body = InterruptedStream()
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(failure, match="interrupted"):
            stream_chat(client, "http://test", request_payload("Hello", 16))
    assert body.closed


def test_fragmented_unicode_stream_can_finish_before_a_separate_usage_event() -> None:
    content = (
        event({"choices": [{"delta": {"content": "tiếng "}}]})
        + event({"choices": [{"delta": {"content": "Việt 👋"}}]})
        + event({"choices": [{"delta": {}, "finish_reason": "length"}]})
        + event(
            {
                "choices": [],
                "usage": {"prompt_tokens": 8, "completion_tokens": 3},
                "timings": {"predicted_per_second": 6.0},
            }
        )
        + "data: [DONE]\n\n"
    )

    class FragmentedStream(httpx.SyncByteStream):
        def __iter__(self):
            for byte in content.encode():
                yield bytes([byte])

    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            stream=FragmentedStream(),
        )
    )
    with httpx.Client(transport=transport) as client:
        result = stream_chat(client, "http://test", request_payload("Hello", 3))
    assert result.output == "tiếng Việt 👋"
    assert result.finish_reason == "length"
    assert result.completion_tokens == 3
    assert result.content_chunks == 2


@pytest.mark.parametrize(
    "content,content_type",
    [
        (stream_fixture(), "application/json"),
        (stream_fixture().replace("data: [DONE]\n\n", ""), "text/event-stream"),
        (
            stream_fixture()
            .replace('"content": "Hello "', '"content": ""')
            .replace('"content": "Tommy"', '"content": ""'),
            "text/event-stream",
        ),
        (
            stream_fixture().replace('"finish_reason": "stop"', '"finish_reason": null'),
            "text/event-stream",
        ),
    ],
    ids=["wrong-media-type", "missing-terminator", "no-visible-text", "missing-finish"],
)
def test_stream_is_not_success_without_the_documented_completion_evidence(
    content: str, content_type: str
) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"content-type": content_type}, content=content)
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(StreamProtocolError):
            stream_chat(client, "http://test", request_payload("Hello", 16))


@pytest.mark.parametrize(
    "field,value",
    [
        ("completion_tokens", 0),
        ("completion_tokens", -1),
        ("completion_tokens", 17),
        ("completion_tokens", 1.5),
        ("completion_tokens", "3"),
        ("prompt_tokens", 0),
        ("prompt_tokens", None),
        ("prompt_tokens", "12"),
    ],
)
def test_invalid_runtime_usage_cannot_be_recorded_as_a_measurement(
    field: str, value: object
) -> None:
    usage = {"prompt_tokens": 12, "completion_tokens": 3, field: value}
    content = (
        event(
            {
                "choices": [{"delta": {"content": "4"}, "finish_reason": "stop"}],
                "usage": usage,
            }
        )
        + "data: [DONE]\n\n"
    )
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=content
        )
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(StreamProtocolError, match="count"):
            stream_chat(client, "http://test", request_payload("Hello", 16))


def test_summary_excludes_warmup_and_separates_prompts() -> None:
    base = {
        "profile": "baseline",
        "prompt_id": "short",
        "phase": "measured",
        "ttft_seconds": 0.2,
        "total_seconds": 1.0,
        "completion_tokens": 3,
        "effective_tokens_per_second": 3.0,
        "finish_reason": "stop",
        "timings": {"predicted_per_second": 6.0},
        "memory": {"peak_rss_bytes": 1234},
        "server_cpu_time_seconds": 0.25,
        "server_cpu_percent": 25.0,
    }
    rows = [
        base,
        {**base, "ttft_seconds": 0.4, "server_cpu_percent": 75.0},
        {**base, "phase": "warmup", "ttft_seconds": 99},
        {**base, "prompt_id": "long"},
    ]
    summary = summarize(rows)
    assert len(summary) == 2
    assert summary[0]["requests"] == 2
    assert summary[0]["median_ttft_seconds"] == pytest.approx(0.3)
    assert summary[0]["peak_server_rss_bytes"] == 1234
    assert summary[0]["mean_server_cpu_percent"] == pytest.approx(50.0)
    assert math.isfinite(summary[0]["mean_server_cpu_percent"])
    assert summary[0]["mean_server_cpu_percent"] >= 0


def test_summary_marks_unavailable_cpu_utilization_without_fabricating_a_value() -> None:
    sample = {
        "profile": "baseline",
        "prompt_id": "short",
        "phase": "measured",
        "ttft_seconds": 0.2,
        "total_seconds": 1.0,
        "completion_tokens": 3,
        "effective_tokens_per_second": 3.0,
        "finish_reason": "stop",
        "timings": {},
        "memory": {},
    }
    summary = summarize([sample])
    assert summary[0]["mean_server_cpu_percent"] is None


def test_committed_benchmark_configs_are_valid() -> None:
    for name in ("baseline", "smoke"):
        config = read_config(ROOT / f"benchmarks/configs/{name}.json")
        assert config["warmups"] >= 1


@pytest.mark.parametrize(
    "change", ["zero_repeats", "duplicate_profile", "empty_prompt", "bad_type"]
)
def test_invalid_experiments_rejected(tmp_path: Path, change: str) -> None:
    config = read_config(ROOT / "benchmarks/configs/smoke.json")
    if change == "zero_repeats":
        config["repetitions"] = 0
    elif change == "duplicate_profile":
        config["profiles"] *= 2
    elif change == "empty_prompt":
        config["prompts"][0]["text"] = "  "
    elif change == "bad_type":
        config["profiles"][0]["threads"] = "two"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        read_config(path)
