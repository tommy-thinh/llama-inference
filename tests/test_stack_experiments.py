"""Independent local-stack experiment contracts from docs/benchmarks.md, using mock boundaries."""

import json
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from benchmarks.scripts import stack_experiment as stack
from inference.artifacts import ROOT


def completed_attempt(latency=1.0, tokens=4, wait=0.2):
    return {
        "success": True,
        "rejected": False,
        "total_seconds": latency,
        "ttft_seconds": 0.5,
        "queue_wait_seconds": wait,
        "usage": {"prompt_tokens": 7, "completion_tokens": tokens},
    }


def test_stack_summary_uses_all_attempts_and_measured_wave_wall_time():
    good = completed_attempt()
    rejection = {"success": False, "rejected": True, "total_seconds": 0.1}
    failure = {"success": False, "rejected": False, "total_seconds": 0.3}
    waves = [
        {
            "strategy": "reject",
            "clients": 4,
            "phase": "warmup",
            "wall_seconds": 100,
            "attempts": [completed_attempt(99, 1000)],
        },
        {
            "strategy": "reject",
            "clients": 4,
            "phase": "measured",
            "wall_seconds": 2,
            "attempts": [good, good, rejection, failure],
        },
        {
            "strategy": "reject",
            "clients": 4,
            "phase": "measured",
            "wall_seconds": 3,
            "attempts": [completed_attempt(2), rejection, rejection, failure],
        },
    ]
    summary = stack.summarize(waves)
    assert len(summary) == 1
    row = summary[0]
    assert row["waves"] == 2 and row["attempts"] == 8
    assert (row["successes"], row["rejections"], row["errors"]) == (3, 3, 2)
    assert row["rejection_rate"] == pytest.approx(3 / 8)
    assert row["wall_seconds"] == 5
    assert row["successful_requests_per_second"] == pytest.approx(3 / 5)
    assert row["completion_tokens_per_second"] == pytest.approx(12 / 5)
    assert row["successful_latency_seconds"]["mean"] == pytest.approx(4 / 3)
    assert row["queue_wait_seconds"]["median"] == 0.2


def test_stack_percentile_is_nearest_rank_including_small_samples():
    assert stack.distribution([]) == {"mean": None, "median": None, "p95": None}
    assert stack.distribution([1])["p95"] == 1
    assert stack.distribution(list(range(1, 21)))["p95"] == 19
    assert stack.distribution([3, 1, 2])["p95"] == 3


def stream_result(events, *, status=200, headers=None, body=None):
    content = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    content += "data: [DONE]\n\n"
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            status,
            headers={"content-type": "text/event-stream", **(headers or {})},
            content=content if body is None else body,
        )
    )
    with httpx.Client(base_url="https://127.0.0.1", transport=transport) as client:
        return stack.stream_attempt(
            client,
            "/api/conversations/fixture",
            {"content": "2+2?", "max_tokens": 8},
            SimpleNamespace(wait=lambda **_: None),
        )


def valid_events():
    return [
        {"conversation_id": "fixture", "request_id": "request", "queue_wait_seconds": 0.125},
        {"choices": [{"delta": {"role": "assistant", "content": None}}]},
        {"choices": [{"delta": {"content": "4"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"usage": {"prompt_tokens": 7, "completion_tokens": 2}},
    ]


def test_stack_parser_keeps_server_wait_and_marks_missing_native_timings_unavailable():
    row = stream_result(valid_events())
    assert row["success"]
    assert row["output"] == "4"
    assert row["queue_wait_seconds"] == 0.125
    assert row["metadata"]["request_id"] == "request"
    assert row["timings"] == {}
    assert row["frames"][-1] == "data: [DONE]\n\n"
    assert row["ttft_seconds"] <= row["total_seconds"]


def test_stack_parser_accepts_null_delta_in_finish_frame():
    events = valid_events()
    events[3]["choices"][0]["delta"] = None
    assert stream_result(events)["success"]


@pytest.mark.parametrize("invalid", [None, True, -0.01, "0.2"])
def test_stack_cannot_infer_queue_wait_when_server_interval_is_invalid(invalid):
    events = valid_events()
    events[0]["queue_wait_seconds"] = invalid
    row = stream_result(events)
    assert not row["success"]
    assert row["error"]


def test_stack_missing_usage_is_a_failed_attempt_with_preserved_output():
    row = stream_result(valid_events()[:-1])
    assert not row["success"]
    assert row["output"] == "4"
    assert row["usage"] == {}
    assert row["error"]


@pytest.mark.parametrize(
    "status,detail,rejected",
    [
        (503, "Inference queue is full", True),
        (503, "Inference queue wait expired", True),
        (503, "Service unavailable", False),
        (400, "bad request", False),
        (500, "error", False),
    ],
)
def test_stack_rejections_and_upstream_errors_remain_distinct(status, detail, rejected):
    row = stream_result(
        [], status=status, headers={"Retry-After": "1"}, body=json.dumps({"detail": detail})
    )
    assert not row["success"]
    assert row["http_status"] == status
    assert row["rejected"] is rejected
    assert row["total_seconds"] >= 0


@pytest.mark.parametrize(
    "change",
    ["zero-warmups", "two-repeats", "boolean-clients", "foreign-strategy", "unknown-key"],
)
def test_stack_config_rejects_uncontrolled_experiments(tmp_path, change):
    config = json.loads((ROOT / "benchmarks/configs/phase5-stack.json").read_text())
    if change == "zero-warmups":
        config["warmups"] = 0
    elif change == "two-repeats":
        config["repetitions"] = 2
    elif change == "boolean-clients":
        config["clients"] = [True, 2, 4]
    elif change == "foreign-strategy":
        config["strategies"]["reject"] = 1
    else:
        config["public_address"] = "0.0.0.0"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        stack.read_config(path)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://fixture:fixture@127.0.0.1/llama",
        "postgresql+psycopg://fixture:fixture@db.example/llama_test",
        "postgresql://fixture:fixture@127.0.0.1/llama_test",
        "sqlite:///fixture_test",
    ],
)
def test_stack_database_boundary_refuses_application_or_remote_database(url, monkeypatch):
    monkeypatch.setenv("LLAMA_TEST_DATABASE_URL", url)
    monkeypatch.setattr(
        stack, "create_engine", lambda *a, **k: pytest.fail("Unsafe database was touched")
    )
    with pytest.raises(ValueError):
        with stack.isolated_database():
            pytest.fail("Unsafe database context was entered")


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_owned_stack_process_is_reaped_after_error_or_cancellation(tmp_path, monkeypatch, failure):
    events = []

    class Process:
        pid = 123
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            events.append("terminate")

        def wait(self, timeout):
            events.append(("wait", timeout))
            self.returncode = 0

    process = Process()
    monkeypatch.setattr(stack.subprocess, "Popen", lambda *a, **k: process)
    record = {}
    with pytest.raises(failure):
        with stack.managed_process(["fixture"], {}, tmp_path / "process.log", record):
            raise failure("fixture failure")
    assert events == ["terminate", ("wait", 15)]
    assert record["exit_code"] == 0


def test_owned_stack_process_is_killed_and_reaped_if_graceful_shutdown_times_out(
    tmp_path, monkeypatch
):
    events = []

    class Process:
        pid = 123
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            events.append("terminate")

        def kill(self):
            events.append("kill")

        def wait(self, timeout):
            events.append(("wait", timeout))
            if timeout == 15:
                raise subprocess.TimeoutExpired("fixture", timeout)
            self.returncode = -9

    monkeypatch.setattr(stack.subprocess, "Popen", lambda *a, **k: Process())
    record = {}
    with stack.managed_process(["fixture"], {}, tmp_path / "process.log", record):
        pass
    assert events == ["terminate", ("wait", 15), "kill", ("wait", 5)]
    assert record["exit_code"] == -9


def test_wave_throughput_clock_excludes_report_serialization(monkeypatch):
    clock = [0.0]

    class Client:
        def post(self, *args, **kwargs):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"id": "fixture"})

    def attempt(client, path, payload, barrier):
        barrier.wait(timeout=2)
        clock[0] = 1.0
        return {**completed_attempt(), "finished_monotonic": clock[0]}

    def slow_progress():
        clock[0] += 100.0

    monkeypatch.setattr(stack, "time", SimpleNamespace(perf_counter=lambda: clock[0]))
    monkeypatch.setattr(stack, "snapshot", lambda _: {})
    monkeypatch.setattr(stack, "stream_attempt", attempt)
    wave = stack.measure_wave(Client(), {}, 1, {}, progress=slow_progress)
    assert wave["wall_seconds"] == pytest.approx(1.0)
    assert clock[0] == 100.0 + 1.0
