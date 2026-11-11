import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from benchmarks.scripts import run
from benchmarks.scripts.metrics import StreamProtocolError
from inference.artifacts import load_lock, sha256_file


@pytest.fixture
def benchmark_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Replace model/OS boundaries; run the real runner, HTTP client, SSE parser and writer."""
    lock = load_lock()
    version = f"version: 0.7.0-dev (build 1, commit {lock['runtime']['commit'][:7]})"
    binary = tmp_path / "llama-server"
    binary.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n")
    binary.chmod(0o755)
    model = tmp_path / "model.gguf"
    model.touch()
    cache = tmp_path / ".runtime/llama.cpp/build/CMakeCache.txt"
    cache.parent.mkdir(parents=True)
    cache.write_text("GGML_CUDA:BOOL=OFF\n")
    environment_manifest = Path(__file__).resolve().parents[1] / "environment.yml"
    (tmp_path / "environment.yml").write_bytes(environment_manifest.read_bytes())
    lock_path = tmp_path / "inference/artifacts.lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(json.dumps(lock, sort_keys=True))
    configuration = {
        "schema_version": 1,
        "warmups": 1,
        "repetitions": 2,
        "profiles": [{"name": "small", "threads": 1, "ctx_size": 1024, "max_tokens": 16}],
        "prompts": [{"id": "arithmetic", "text": "What is 2 + 2?"}],
    }
    config_path = tmp_path / "experiment.json"
    config_path.write_text(json.dumps(configuration))
    state = SimpleNamespace(
        config_path=config_path,
        output_path=tmp_path / "results/raw.json",
        binary=binary,
        version=version,
        active=False,
        events=[],
        payloads=[],
        fail_request=None,
        startup_error=None,
        exit_code=0,
    )
    for key, value in {
        "LLAMA_SERVER_PATH": str(binary),
        "LLAMA_MODEL_PATH": str(model),
        "LLAMA_THREADS": "1",
        "LLAMA_CTX_SIZE": "1024",
        "LLAMA_MAX_TOKENS": "16",
        "LLAMA_PORT": "18080",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(run, "ROOT", tmp_path)
    monkeypatch.setattr(
        run,
        "verify_model",
        lambda _: {
            "sha256": lock["model"]["sha256"],
            "size_bytes": lock["model"]["size_bytes"],
            "metadata": {"general.architecture": "llama"},
        },
    )
    monkeypatch.setattr(run, "check_runtime", lambda _: lock["runtime"]["commit"])
    monkeypatch.setattr(run, "hardware_snapshot", lambda: {"machine": "controlled-test-machine"})
    monkeypatch.setattr(
        run, "memory_snapshot", lambda _: {"rss_bytes": 2048, "peak_rss_bytes": 4096}
    )
    monkeypatch.setattr(run, "process_cpu_seconds", lambda _: 0.0)
    ports = iter(range(18081, 18090))
    monkeypatch.setattr(run, "free_local_port", lambda: next(ports))

    class OwnedServer:
        def __init__(self, config: object, log_path: Path) -> None:
            self.config = config
            self.startup_seconds = 0.125
            self.pid = 12345
            self.process = SimpleNamespace(returncode=None)

        def __enter__(self) -> "OwnedServer":
            assert not state.active, "Benchmark profiles must run serially"
            if state.startup_error is not None:
                raise state.startup_error
            state.active = True
            state.events.append(("start", self.config.port))
            return self

        def __exit__(self, *args: object) -> None:
            state.active = False
            self.process.returncode = state.exit_code
            state.events.append(("stop", self.config.port))

    def response(request: httpx.Request) -> httpx.Response:
        assert state.active, "Requests must only use the currently owned server"
        if request.url.path == "/props":
            return httpx.Response(200, json={"test_configuration": True})
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        state.payloads.append(payload)
        state.events.append(("request", request.url.port))
        events = [{"choices": [{"delta": {"content": "4"}}]}]
        if len(state.payloads) != state.fail_request:
            events.append(
                {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 2},
                    "timings": {"predicted_per_second": 6.0, "cache_n": 0},
                }
            )
        content = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
        if len(state.payloads) != state.fail_request:
            content += "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content)

    client_class = httpx.Client
    monkeypatch.setattr(run, "ManagedServer", OwnedServer)
    monkeypatch.setattr(
        run.httpx, "Client", lambda **kwargs: client_class(transport=httpx.MockTransport(response))
    )
    return state


def test_runner_records_serial_profiles_and_reproducible_evidence(
    benchmark_environment: SimpleNamespace,
) -> None:
    state = benchmark_environment
    configuration = json.loads(state.config_path.read_text())
    configuration["profiles"].append(
        {"name": "larger", "threads": 2, "ctx_size": 2048, "max_tokens": 32}
    )
    configuration["prompts"].append({"id": "long", "text": "Longer arithmetic prompt: 2 + 2?"})
    state.config_path.write_text(json.dumps(configuration))
    report = run.run_benchmark(state.config_path, state.output_path)
    assert json.loads(state.output_path.read_text()) == report
    assert report["status"] == "complete"
    assert report["configuration"] == configuration
    assert report["configuration_sha256"] == sha256_file(state.config_path)
    assert report["runtime_commit"] == load_lock()["runtime"]["commit"]
    assert report["runtime_version"] == state.version
    assert report["runtime_binary_sha256"] == sha256_file(state.binary)
    assert report["verified_model_sha256"] == load_lock()["model"]["sha256"]
    assert report["gguf"]["size_bytes"] == load_lock()["model"]["size_bytes"]
    assert report["artifacts"]["model"]["upstream"] == "meta-llama/Llama-3.2-1B-Instruct"
    assert report["artifacts"]["model"]["quantization"] == "Q4_K_M"
    assert report["source_sha256"]["environment.yml"] == sha256_file(
        state.config_path.parent / "environment.yml"
    )
    assert "uv.lock" not in report["source_sha256"]
    assert report["source_sha256"]["inference/artifacts.lock.json"] == sha256_file(
        state.config_path.parent / "inference/artifacts.lock.json"
    )
    assert report["hardware"] == {"machine": "controlled-test-machine"}
    assert report["cmake_cache"] == "GGML_CUDA:BOOL=OFF\n"
    assert report["started_at"] <= report["finished_at"]
    assert len(report["requests"]) == 12
    assert sum(row["phase"] == "warmup" for row in report["requests"]) == 4
    assert len(report["summary"]) == 4
    assert all(row["requests"] == 2 for row in report["summary"])
    assert all(row["peak_server_rss_bytes"] == 4096 for row in report["summary"])
    assert all(payload["model"] == "llama-3.2-1b-instruct" for payload in state.payloads)
    for request in report["requests"]:
        assert math.isfinite(request["server_cpu_time_seconds"])
        assert request["server_cpu_time_seconds"] >= 0
        assert math.isfinite(request["server_cpu_percent"])
        assert request["server_cpu_percent"] >= 0
    assert all(row["mean_server_cpu_percent"] == 0 for row in report["summary"])
    assert [profile["settings"]["max_tokens"] for profile in report["profiles"]] == [16, 32]
    for profile in report["profiles"]:
        assert profile["server_exit_code"] == 0
        assert profile["startup_seconds"] == 0.125
        assert profile["memory_after_startup"]["rss_bytes"] == 2048
        command = profile["command"]
        for flag, value in (("--host", "127.0.0.1"), ("--parallel", "1"), ("--n-gpu-layers", "0")):
            assert command[command.index(flag) + 1] == value
    assert [event[0] for event in state.events] == ["start", *["request"] * 6, "stop"] * 2
    assert not state.active
    assert not state.output_path.with_suffix(".json.tmp").exists()


def test_full_baseline_config_covers_multiple_thread_and_context_settings() -> None:
    configuration = run.read_config(run.ROOT / "benchmarks/configs/baseline.json")
    assert len({profile["threads"] for profile in configuration["profiles"]}) > 1
    assert len({profile["ctx_size"] for profile in configuration["profiles"]}) > 1


def test_hardware_snapshot_records_conda_environment_without_uv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "llama")
    monkeypatch.setenv("CONDA_PREFIX", "/test/conda/envs/llama")
    commands: list[list[str]] = []

    def check_output(command: list[str], **_: object) -> str:
        commands.append(command)
        if command[0] == "uv":
            raise FileNotFoundError("uv is not installed")
        if command[0] == "lscpu":
            return '{"lscpu": []}'
        return f"{command[0]} test version"

    monkeypatch.setattr(run.subprocess, "check_output", check_output)

    snapshot = run.hardware_snapshot()

    assert not any(command[0] == "uv" for command in commands)
    assert "uv_version" not in snapshot
    assert snapshot["python_environment"] == {
        "conda_name": "llama",
        "conda_prefix": "/test/conda/envs/llama",
        "python_prefix": sys.prefix,
    }


def test_runner_preserves_previous_evidence_without_starting_a_server(
    benchmark_environment: SimpleNamespace,
) -> None:
    state = benchmark_environment
    state.output_path.parent.mkdir()
    state.output_path.write_bytes(b"previous experiment evidence\n")
    with pytest.raises(FileExistsError):
        run.run_benchmark(state.config_path, state.output_path)
    assert state.output_path.read_bytes() == b"previous experiment evidence\n"
    assert state.events == []


def test_runner_keeps_partial_evidence_and_cleans_up_a_disconnected_stream(
    benchmark_environment: SimpleNamespace,
) -> None:
    state = benchmark_environment
    state.fail_request = 3
    with pytest.raises(StreamProtocolError, match="without.*DONE"):
        run.run_benchmark(state.config_path, state.output_path)
    report = json.loads(state.output_path.read_text())
    assert report["status"] == "failed"
    assert "StreamProtocolError" in report["error"]
    assert report["finished_at"]
    assert len(report["requests"]) == 2
    assert report["summary"][0]["requests"] == 1
    assert state.events[-1][0] == "stop"
    assert not state.active
    assert not state.output_path.with_suffix(".json.tmp").exists()


def test_runner_records_startup_failure_as_failed_evidence(
    benchmark_environment: SimpleNamespace,
) -> None:
    state = benchmark_environment
    state.startup_error = TimeoutError("owned server never became ready")
    with pytest.raises(TimeoutError, match="never became ready"):
        run.run_benchmark(state.config_path, state.output_path)
    report = json.loads(state.output_path.read_text())
    assert report["status"] == "failed"
    assert "TimeoutError" in report["error"]
    assert report["requests"] == []
    assert report["summary"] == []
    assert not state.active


def test_benchmark_rejects_the_wrong_executable_before_running_profiles(
    benchmark_environment: SimpleNamespace,
) -> None:
    state = benchmark_environment
    state.binary.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'version: 0.7.0-dev (build 1, commit 0000000)'\n"
    )
    with pytest.raises(ValueError):
        run.run_benchmark(state.config_path, state.output_path)
    assert state.events == []
    assert not state.output_path.exists()
