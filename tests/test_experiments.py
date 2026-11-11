"""Independent Phase 5 measurement contracts from PROJECT_SPEC and docs/13.

Model and process boundaries are mocked here. Performance measurements belong
to the serial real experiments, not this deterministic contract suite.
"""

import copy
import hashlib
import json
from contextlib import contextmanager

import httpx
import pytest

from benchmarks.scripts.metrics import StreamProtocolError, stream_chat
from benchmarks.scripts.run import request_payload
from inference.artifacts import load_lock


def measure_events(events):
    content = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    content += "data: [DONE]\n\n"
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=content
            )
        )
    ) as client:
        return stream_chat(client, "http://test", request_payload("What is 2 + 2?", 8))


def test_native_cache_counters_are_recorded_without_inference_from_latency():
    timings = {
        "cache_n": 117,
        "prompt_n": 3,
        "prompt_ms": 24.5,
        "predicted_n": 2,
        "predicted_ms": 50.0,
        "predicted_per_second": 40.0,
    }
    result = measure_events(
        [
            {"choices": [{"delta": {"content": "4"}}]},
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 2},
                "timings": timings,
            },
        ]
    )
    assert result.timings == timings


def test_missing_native_cache_counters_remain_unavailable():
    result = measure_events(
        [
            {
                "choices": [{"delta": {"content": "4"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2},
            }
        ]
    )
    assert "cache_n" not in result.timings
    assert "prompt_n" not in result.timings


def test_null_role_content_and_finish_delta_do_not_count_as_visible_tokens():
    result = measure_events(
        [
            {"choices": [{"delta": {"role": "assistant", "content": None}}]},
            {"choices": [{"delta": {"content": "4"}}], "usage": None},
            {"choices": [{"delta": None, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}},
        ]
    )
    assert result.output == "4"
    assert result.content_chunks == 1
    assert result.completion_tokens == 2


@pytest.mark.parametrize("field", ["prompt_tokens", "completion_tokens"])
def test_boolean_usage_is_not_a_runtime_token_count(field):
    with pytest.raises(StreamProtocolError, match="count"):
        measure_events(
            [
                {
                    "choices": [{"delta": {"content": "4"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 2, field: True},
                }
            ]
        )


def test_experiment_artifacts_preserve_active_q4_identity_and_runtime():
    from benchmarks.scripts.experiment_artifacts import load_manifest

    manifest = load_manifest()
    active = load_lock()
    assert manifest["runtime"] == active["runtime"]
    assert all(manifest["models"]["q4"][key] == value for key, value in active["model"].items())
    q8 = manifest["models"]["q8"]
    assert q8["upstream"] == active["model"]["upstream"]
    assert q8["revision"] == active["model"]["revision"]
    assert q8["quantization"] == "Q8_0"
    assert q8["filename"] != active["model"]["filename"]
    assert q8["sha256"] != active["model"]["sha256"]
    assert q8["size_bytes"] > active["model"]["size_bytes"]


@pytest.fixture
def experimental_artifact(tmp_path, monkeypatch):
    from benchmarks.scripts import experiment_artifacts as module

    content = b"independent synthetic model fixture"
    template = "<|begin_of_text|> independent Llama template <|eot_id|>"
    expected = copy.deepcopy(module.load_manifest()["models"]["q8"])
    expected.update(
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        chat_template_sha256=hashlib.sha256(template.encode()).hexdigest(),
    )
    header = {
        "gguf_version": 3,
        "tensor_count": 147,
        "metadata": {
            "general.architecture": "llama",
            **expected["gguf_metadata"],
            "tokenizer.chat_template": template,
            "tokenizer.ggml.tokens": {
                "length": 128256,
                "special_tokens": list(expected["chat_template_markers"]),
            },
        },
    }
    path = tmp_path / "q8.gguf"
    path.write_bytes(content)
    monkeypatch.setattr(module, "load_manifest", lambda: {"models": {"q8": expected}})
    monkeypatch.setattr(module, "read_gguf_metadata", lambda _: copy.deepcopy(header))
    monkeypatch.setattr(module, "model_path", lambda _: path)
    return module, path, content, expected, header


def test_additional_artifact_verification_records_verified_bytes_and_header(experimental_artifact):
    module, path, content, expected, header = experimental_artifact
    verified = module.verify_experiment_model(path, "q8")
    assert verified["sha256"] == hashlib.sha256(content).hexdigest()
    assert verified["size_bytes"] == len(content)
    assert verified["metadata"] == header["metadata"]


@pytest.mark.parametrize(
    "corruption",
    ["size", "checksum", "architecture", "tokenizer", "gqa", "template", "vocabulary", "markers"],
)
def test_additional_artifact_rejects_changed_bytes_or_model_identity(
    experimental_artifact, corruption
):
    module, path, content, expected, header = experimental_artifact
    metadata = header["metadata"]
    if corruption == "size":
        path.write_bytes(content + b"x")
    elif corruption == "checksum":
        path.write_bytes(b"x" * len(content))
    elif corruption == "architecture":
        metadata["general.architecture"] = "qwen35"
    elif corruption == "tokenizer":
        metadata["tokenizer.ggml.pre"] = "other-bpe"
    elif corruption == "gqa":
        metadata["llama.attention.head_count_kv"] = 32
    elif corruption == "template":
        metadata["tokenizer.chat_template"] += " changed prompt semantics"
    elif corruption == "vocabulary":
        metadata["tokenizer.ggml.tokens"]["length"] -= 1
    else:
        metadata["tokenizer.ggml.tokens"]["special_tokens"].pop()
    with pytest.raises(ValueError):
        module.verify_experiment_model(path, "q8")


def test_artifact_download_never_replaces_existing_invalid_evidence(experimental_artifact):
    module, path, _, _, _ = experimental_artifact
    path.write_bytes(b"existing evidence")
    with pytest.raises(ValueError):
        module.download_model("q8")
    assert path.read_bytes() == b"existing evidence"


def test_artifact_download_removes_owned_partial_after_connection_failure(
    experimental_artifact, monkeypatch
):
    module, path, _, _, _ = experimental_artifact
    path.unlink()

    @contextmanager
    def disconnected(*args, **kwargs):
        raise httpx.ConnectError("fixture network failure")
        yield

    monkeypatch.setattr(module.httpx, "stream", disconnected)
    with pytest.raises(httpx.ConnectError, match="fixture network failure"):
        module.download_model("q8")
    assert not path.exists()
    assert not list(path.parent.glob("*.partial"))


def native_config():
    return {
        "schema_version": 1,
        "warmups": 1,
        "repetitions": 3,
        "profiles": [{"name": "controlled"}],
        "prompts": [{"id": "short", "text": "What is 2 + 2?"}],
        "quality": [{"id": "arithmetic", "prompt": "What is 2 + 2?", "pattern": "^4$"}],
    }


def test_native_commands_keep_context_per_slot_constant_and_defaults_isolated(tmp_path):
    from benchmarks.scripts.experiments import ExperimentServerConfig
    from inference.config import ServerConfig

    production = ServerConfig(binary=tmp_path / "server", model=tmp_path / "q4")
    original = production.command()
    for slots in (1, 2, 4):
        experiment = ExperimentServerConfig(
            binary=production.binary,
            model=tmp_path / "q8",
            ctx_size=2048,
            parallel=slots,
            max_tokens=32,
            kv_type="q8_0",
            batch_size=512,
            ubatch_size=256,
            cont_batching=False,
        )
        command = experiment.command()
        for flag, value in {
            "--ctx-size": str(2048 * slots),
            "--parallel": str(slots),
            "--host": "127.0.0.1",
            "--n-gpu-layers": "0",
            "--cache-type-k": "q8_0",
            "--cache-type-v": "q8_0",
            "--batch-size": "512",
            "--ubatch-size": "256",
        }.items():
            assert command.count(flag) == 1
            assert command[command.index(flag) + 1] == value
        assert "--no-cont-batching" in command
    assert production.command() == original
    assert production.ctx_size == 4096
    assert original[original.index("--cache-type-k") + 1] == "f16"
    assert original[original.index("--parallel") + 1] == "1"


@pytest.mark.parametrize(
    "changes",
    [
        {"kv_type": "imaginary"},
        {"parallel": True},
        {"parallel": 3},
        {"batch_size": 128, "ubatch_size": 256},
        {"batch_size": True},
        {"flash_attn": 1},
        {"cont_batching": "false"},
        {"kv_type": "q4_0", "flash_attn": False},
    ],
)
def test_native_configuration_rejects_unsupported_or_ambiguous_values(tmp_path, changes):
    from benchmarks.scripts.experiments import ExperimentServerConfig

    with pytest.raises(ValueError):
        ExperimentServerConfig(binary=tmp_path / "server", model=tmp_path / "model", **changes)


@pytest.mark.parametrize(
    "change",
    [
        "boolean-schema",
        "warmup-zero",
        "repeats-two",
        "model-substitution",
        "cache-string",
        "unknown-setting",
        "duplicate-profile",
        "blank-prompt",
        "missing-quality",
    ],
)
def test_native_config_enforces_controlled_measurement_contract(tmp_path, change):
    from benchmarks.scripts.experiments import read_config

    config = native_config()
    if change == "boolean-schema":
        config["schema_version"] = True
    elif change == "warmup-zero":
        config["warmups"] = 0
    elif change == "repeats-two":
        config["repetitions"] = 2
    elif change == "model-substitution":
        config["profiles"][0]["model"] = "qwen"
    elif change == "cache-string":
        config["profiles"][0]["cache_prompt"] = "false"
    elif change == "unknown-setting":
        config["profiles"][0]["gpu_layers"] = 1
    elif change == "duplicate-profile":
        config["profiles"] *= 2
    elif change == "blank-prompt":
        config["prompts"][0]["text"] = " "
    else:
        del config["quality"]
    path = tmp_path / "configuration.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        read_config(path)


def test_native_summary_excludes_warmups_and_uses_wave_wall_time_and_all_attempts():
    from benchmarks.scripts.report_experiments import summarize

    def row(phase, success, latency=1, tokens=4, timings=None):
        return {
            "phase": phase,
            "profile": "controlled",
            "prompt_id": "short",
            "clients": 2,
            "success": success,
            "total_seconds": latency,
            "ttft_seconds": latency / 2,
            "completion_tokens": tokens,
            "timings": timings or {},
        }

    def wave(phase, seconds):
        return {
            "phase": phase,
            "profile": "controlled",
            "prompt_id": "short",
            "clients": 2,
            "wall_seconds": seconds,
            "server_cpu_percent": None,
            "memory": {},
        }

    report = {
        "requests": [
            row("warmup", True, 99, 1000, {"cache_n": 99}),
            row("measured", True, 1, 4),
            row("measured", True, 3, 6),
            row("measured", False),
        ],
        "waves": [wave("warmup", 100), wave("measured", 2), wave("measured", 3)],
    }
    result = summarize(report)[0]
    assert (result["attempts"], result["successes"], result["errors"]) == (3, 2, 1)
    assert result["error_rate"] == pytest.approx(1 / 3)
    assert result["mean_latency_seconds"] == 2
    assert result["median_latency_seconds"] == 2
    assert result["p95_latency_seconds"] == 3
    assert result["throughput_requests_per_second"] == pytest.approx(2 / 5)
    assert result["throughput_tokens_per_second"] == pytest.approx(10 / 5)
    assert result["mean_cache_tokens"] is None
    assert result["cache_counter_samples"] == 0


def test_native_nearest_rank_p95_on_small_and_larger_samples():
    from benchmarks.scripts.report_experiments import percentile

    assert percentile([3, 1, 2]) == 3
    assert percentile(list(range(1, 21))) == 19


def test_native_existing_report_is_never_overwritten(tmp_path):
    from benchmarks.scripts.experiments import run

    config = tmp_path / "config.json"
    config.write_text(json.dumps(native_config()))
    output = tmp_path / "result.json"
    output.write_text("existing evidence")
    with pytest.raises(FileExistsError):
        run(config, output)
    assert output.read_text() == "existing evidence"


def test_native_initialization_failure_preserves_failure_record(tmp_path, monkeypatch):
    from benchmarks.scripts import experiments

    config = tmp_path / "config.json"
    config.write_text(json.dumps(native_config()))
    output = tmp_path / "result.json"
    monkeypatch.setattr(
        experiments,
        "check_runtime",
        lambda *_: (_ for _ in ()).throw(RuntimeError("fixture runtime failure")),
    )
    with pytest.raises(RuntimeError, match="fixture runtime failure"):
        experiments.run(config, output)
    report = json.loads(output.read_text())
    assert report["status"] in {"failed", "interrupted"}
    assert report["finished_at"]


def test_native_interruption_saves_completed_wave_and_closes_owned_server(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from benchmarks.scripts import experiments

    config = tmp_path / "config.json"
    config.write_text(json.dumps(native_config()))
    output = tmp_path / "result.json"
    events = []

    class Server:
        def __init__(self, *args, **kwargs):
            self.pid = 42
            self.process = SimpleNamespace(returncode=None)
            self.startup_seconds = 0.1

        def __enter__(self):
            events.append("started")
            return self

        def __exit__(self, *args):
            self.process.returncode = 0
            events.append("stopped")

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args):
            return SimpleNamespace(json=lambda: {})

    calls = []

    def sample(*args):
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt("fixture cancellation")
        return SimpleNamespace(
            to_dict=lambda: {
                "output": "4",
                "completion_tokens": 1,
                "ttft_seconds": 0.1,
                "total_seconds": 0.2,
                "timings": {},
            }
        )

    monkeypatch.setattr(experiments, "check_runtime", lambda *_: "fixture")
    monkeypatch.setattr(experiments, "checked_runtime_version", lambda *_: "fixture")
    monkeypatch.setattr(experiments, "sha256_file", lambda *_: "fixture")
    monkeypatch.setattr(experiments, "hardware_snapshot", lambda: {})
    monkeypatch.setattr(experiments, "verify_experiment_model", lambda *_: {})
    monkeypatch.setattr(experiments, "free_local_port", lambda: 12345)
    monkeypatch.setattr(experiments, "ManagedServer", Server)
    monkeypatch.setattr(experiments.httpx, "Client", Client)
    monkeypatch.setattr(experiments, "stream_chat", sample)
    monkeypatch.setattr(experiments, "memory_snapshot", lambda *_: {})
    monkeypatch.setattr(experiments, "process_cpu_seconds", lambda *_: 0)

    with pytest.raises(KeyboardInterrupt, match="fixture cancellation"):
        experiments.run(config, output)
    report = json.loads(output.read_text())
    assert report["status"] == "interrupted"
    assert report["finished_at"]
    assert len(report["waves"]) == len(report["requests"]) == 1
    assert report["requests"][0]["phase"] == "warmup"
    assert report["requests"][0]["output"] == "4"
    assert report["profiles"][0]["server_exit_code"] == 0
    assert events == ["started", "stopped"]


def test_incomplete_native_stream_preserves_partial_measurement():
    import math

    content = (
        'data: {"choices":[{"delta":{"content":"Hello"}}],'
        '"usage":{"prompt_tokens":9,"completion_tokens":1},'
        '"timings":{"cache_n":2,"prompt_n":7}}\n\n'
    )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=content
            )
        )
    ) as client:
        with pytest.raises(StreamProtocolError, match="without \\[DONE\\]") as caught:
            stream_chat(client, "http://test", request_payload("fixture", 8))
    partial = caught.value.partial_measurement
    assert partial["output"] == "Hello"
    assert math.isfinite(partial["total_seconds"]) and partial["total_seconds"] >= 0
    assert partial["http_status"] == 200
    assert partial["usage"] == {"prompt_tokens": 9, "completion_tokens": 1}
    assert partial["timings"] == {"cache_n": 2, "prompt_n": 7}


def test_cache_allocation_reads_runtime_reported_buffers_separately_from_rss(tmp_path):
    from benchmarks.scripts.experiments import cache_allocation

    log = tmp_path / "runtime.log"
    log.write_text(
        "llama_kv_cache:        CPU KV buffer size =   112.00 MiB\n"
        "llama_kv_cache: size =  112.00 MiB (  2048 cells,  16 layers,  1/1 seqs), "
        "K (f16):   56.00 MiB, V (f16):   56.00 MiB\n"
        "other memory: 900.00 MiB\n"
    )
    allocation = cache_allocation(log)
    assert allocation["buffer_mib"] == [112.0]
    assert len(allocation["runtime_log_lines"]) == 2
    log.write_text("no runtime KV allocation info\n")
    assert cache_allocation(log)["buffer_mib"] == []


def test_cache_allocation_probe_command_does_not_mutate_production_defaults(tmp_path):
    from benchmarks.scripts.experiments import AllocationConfig
    from inference.config import ServerConfig

    production = ServerConfig(binary=tmp_path / "server", model=tmp_path / "q4")
    before = production.command()
    probe = AllocationConfig(binary=production.binary, model=production.model)
    command = probe.command()
    assert command[command.index("--log-verbosity") + 1] == "4"
    assert "--log-verbosity" not in before
    assert production.command() == before


def test_native_failed_attempt_keeps_partial_response_in_raw_result(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from benchmarks.scripts import experiments

    config = tmp_path / "config.json"
    config.write_text(json.dumps(native_config()))
    output = tmp_path / "result.json"
    partial = {
        "output": "Hello",
        "http_status": 200,
        "total_seconds": 0.2,
        "usage": {"prompt_tokens": 9, "completion_tokens": 1},
        "timings": {"cache_n": 2},
    }

    class Server:
        def __init__(self, *args, **kwargs):
            self.pid = 42
            self.process = SimpleNamespace(returncode=None)
            self.startup_seconds = 0.1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.process.returncode = 0

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args):
            return SimpleNamespace(json=lambda: {})

    calls = []

    def sample(*args):
        calls.append(1)
        if len(calls) <= 4:
            failure = StreamProtocolError("stream ended without DONE")
            failure.partial_measurement = partial
            raise failure
        return SimpleNamespace(to_dict=lambda: {"output": "4"})

    monkeypatch.setattr(experiments, "check_runtime", lambda *_: "fixture")
    monkeypatch.setattr(experiments, "checked_runtime_version", lambda *_: "fixture")
    monkeypatch.setattr(experiments, "sha256_file", lambda *_: "fixture")
    monkeypatch.setattr(experiments, "hardware_snapshot", lambda: {})
    monkeypatch.setattr(experiments, "verify_experiment_model", lambda *_: {})
    monkeypatch.setattr(experiments, "free_local_port", lambda: 12345)
    monkeypatch.setattr(experiments, "ManagedServer", Server)
    monkeypatch.setattr(experiments.httpx, "Client", Client)
    monkeypatch.setattr(experiments, "stream_chat", sample)
    monkeypatch.setattr(experiments, "memory_snapshot", lambda *_: {})
    monkeypatch.setattr(experiments, "process_cpu_seconds", lambda *_: 0)
    monkeypatch.setattr(experiments, "cache_allocation", lambda *_: {})

    report = experiments.run(config, output)
    assert report["status"] == "complete_with_failures"
    assert len(report["requests"]) == 4
    for row in report["requests"]:
        assert row["success"] is False
        assert row["error"] == "StreamProtocolError"
        assert all(row[key] == value for key, value in partial.items())
    assert json.loads(output.read_text())["requests"] == report["requests"]
