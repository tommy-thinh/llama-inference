import errno
import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from inference import server
from inference.config import ServerConfig


def test_server_configuration_is_local_cpu_only() -> None:
    config = ServerConfig.local(threads=1)
    command = config.command()
    for flag, value in (
        ("--host", "127.0.0.1"),
        ("--n-gpu-layers", "0"),
        ("--parallel", "1"),
        ("--reasoning", "off"),
        ("--cache-ram", "0"),
        ("--alias", "llama-3.2-1b-instruct"),
        ("--threads", "1"),
        ("--threads-batch", "1"),
        ("--ctx-size", "2048"),
        ("--n-predict", "128"),
        ("--batch-size", "256"),
        ("--ubatch-size", "128"),
        ("--cache-type-k", "f16"),
        ("--cache-type-v", "f16"),
        ("--ctx-checkpoints", "0"),
    ):
        assert command[command.index(flag) + 1] == value
    assert "--offline" in command
    assert "--no-context-shift" in command
    assert "--jinja" in command
    assert "--no-webui" in command
    assert config.base_url == "http://127.0.0.1:8081"
    assert command[command.index("--model") + 1] == str(config.model)
    assert command.count("--model") == 1


@pytest.mark.parametrize(
    "setting,value",
    [
        ("threads", 0),
        ("ctx_size", 128),
        ("ctx_size", 100000),
        ("max_tokens", 0),
        ("max_tokens", 513),
        ("port", 80),
        ("port", 65536),
    ],
)
def test_invalid_resource_settings_are_rejected(setting: str, value: int) -> None:
    with pytest.raises(ValueError):
        ServerConfig.local(**{setting: value})


def test_inherited_llama_settings_cannot_change_experiment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLAMA_ARG_HOST", "0.0.0.0")
    monkeypatch.setenv("LLAMA_API_KEY", "test")
    monkeypatch.setenv("LLAMA_THREADS", "2")
    environment = server.server_environment()
    assert "LLAMA_ARG_HOST" not in environment
    assert "LLAMA_API_KEY" not in environment
    assert ServerConfig.local().threads == 2


def test_print_command_overrides_invalid_environment_without_starting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("LLAMA_THREADS", "LLAMA_CTX_SIZE", "LLAMA_MAX_TOKENS", "LLAMA_PORT"):
        monkeypatch.setenv(name, "invalid")
    settings = {"--threads": "1", "--ctx-size": "1024", "--max-tokens": "16", "--port": "8082"}
    arguments = ["inference.server", "--print-command"]
    for flag, value in settings.items():
        arguments.extend((flag, value))
    monkeypatch.setattr(sys, "argv", arguments)
    monkeypatch.setattr(
        server.os, "execve", lambda *args: pytest.fail("Print must not start server")
    )
    server.main()
    command = shlex.split(capsys.readouterr().out)
    for flag, value in settings.items():
        runtime_flag = "--n-predict" if flag == "--max-tokens" else flag
        assert command[command.index(runtime_flag) + 1] == value


def mock_process_environment(monkeypatch: pytest.MonkeyPatch, response_code: int) -> MagicMock:
    process = MagicMock()
    process.poll.return_value = None
    monkeypatch.setattr(server.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(server.socket, "socket", MagicMock())
    response = MagicMock(status_code=response_code)
    response.json.return_value = {"status": "ok"}
    client = MagicMock()
    client.__enter__.return_value.get.return_value = response
    monkeypatch.setattr(server.httpx, "Client", lambda *a, **kw: client)
    return process


def test_process_terminated_when_context_body_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_model: Path
) -> None:
    process = mock_process_environment(monkeypatch, 200)
    config = ServerConfig(binary=Path("/usr/bin/true"), model=fixture_model, threads=1)
    owned = server.ManagedServer(config, tmp_path / "server.log")
    with pytest.raises(RuntimeError, match="benchmark failed"):
        with owned:
            raise RuntimeError("benchmark failed")
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=10)
    assert owned._log.closed


def test_startup_timeout_cleans_up_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_model: Path
) -> None:
    process = mock_process_environment(monkeypatch, 503)
    ticks = iter([0.0, 1.0, 2.0, 3.0])
    monkeypatch.setattr(server.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(server.time, "sleep", lambda _: None)
    config = ServerConfig(binary=Path("/usr/bin/true"), model=fixture_model, threads=1)
    with pytest.raises(TimeoutError):
        with server.ManagedServer(config, tmp_path / "server.log", startup_timeout=3):
            pytest.fail("An unready server cannot enter the context")
    process.terminate.assert_called_once()


def test_shutdown_kills_only_owned_process_if_termination_times_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_model: Path
) -> None:
    process = mock_process_environment(monkeypatch, 200)
    process.wait.side_effect = [subprocess.TimeoutExpired("server", 10), 0]
    config = ServerConfig(binary=Path("/usr/bin/true"), model=fixture_model, threads=1)
    with server.ManagedServer(config, tmp_path / "server.log"):
        pass
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert process.wait.call_count == 2


def test_owned_process_is_reaped_and_log_closed_after_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_model: Path
) -> None:
    process = mock_process_environment(monkeypatch, 200)

    def exited(**kwargs: object) -> int:
        process.poll.return_value = 0
        return 0

    process.wait.side_effect = exited
    config = ServerConfig(binary=Path("/usr/bin/true"), model=fixture_model, threads=1)
    owned = server.ManagedServer(config, tmp_path / "server.log")
    with owned as running:
        assert running is owned
        assert not owned._log.closed
    owned.stop()
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=10)
    process.kill.assert_not_called()
    assert owned._log.closed


def test_startup_retries_connection_failure_and_unready_health(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_model: Path
) -> None:
    process = mock_process_environment(monkeypatch, 200)
    client = MagicMock()
    client.__enter__.return_value.get.side_effect = [
        httpx.ConnectError("still starting"),
        httpx.Response(503, json={"status": "loading model"}),
        httpx.Response(200, json={"status": "loading model"}),
        httpx.Response(200, json={"status": "ok"}),
    ]
    monkeypatch.setattr(server.httpx, "Client", lambda *args, **kwargs: client)
    monkeypatch.setattr(server.time, "sleep", lambda _: None)
    config = ServerConfig(binary=Path("/usr/bin/true"), model=fixture_model, threads=1)
    with server.ManagedServer(config, tmp_path / "server.log"):
        assert client.__enter__.return_value.get.call_count == 4
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=10)


def test_child_exit_before_readiness_is_reported_and_log_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_model: Path
) -> None:
    process = mock_process_environment(monkeypatch, 200)
    process.poll.return_value = 7
    config = ServerConfig(binary=Path("/usr/bin/true"), model=fixture_model, threads=1)
    owned = server.ManagedServer(config, tmp_path / "server.log")
    with pytest.raises(RuntimeError, match="exited"):
        with owned:
            pytest.fail("An exited child must not enter the ready context")
    process.terminate.assert_not_called()
    process.kill.assert_not_called()
    assert owned._log.closed


def test_spawn_failure_closes_the_log_and_keeps_the_original_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_model: Path
) -> None:
    monkeypatch.setattr(server.socket, "socket", MagicMock())
    spawn = MagicMock(side_effect=OSError("cannot execute child"))
    monkeypatch.setattr(server.subprocess, "Popen", spawn)
    config = ServerConfig(binary=Path("/usr/bin/true"), model=fixture_model, threads=1)
    owned = server.ManagedServer(config, tmp_path / "server.log")
    with pytest.raises(OSError, match="cannot execute child"):
        with owned:
            pytest.fail("A failed spawn cannot enter the context")
    assert owned._log.closed


def test_occupied_port_does_not_spawn_or_touch_another_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_model: Path
) -> None:
    sock = MagicMock()
    sock.__enter__.return_value.bind.side_effect = OSError(
        errno.EADDRINUSE, "address already in use"
    )
    monkeypatch.setattr(server.socket, "socket", lambda *args: sock)
    spawn = MagicMock()
    monkeypatch.setattr(server.subprocess, "Popen", spawn)
    log_path = tmp_path / "server.log"
    log_path.write_text("existing service diagnostics\n")
    config = ServerConfig(binary=Path("/usr/bin/true"), model=fixture_model, threads=1)
    with pytest.raises(OSError) as exc:
        with server.ManagedServer(config, log_path):
            pytest.fail("An occupied port must not enter the context")
    assert exc.value.errno == errno.EADDRINUSE
    spawn.assert_not_called()
    assert log_path.read_text() == "existing service diagnostics\n"


@pytest.mark.parametrize("missing", ["binary", "executable-permission", "model"])
def test_missing_assets_fail_before_any_child_or_log_is_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_model: Path, missing: str
) -> None:
    binary = Path("/usr/bin/true")
    model = fixture_model
    if missing == "binary":
        binary = tmp_path / "missing-server"
    elif missing == "executable-permission":
        binary = tmp_path / "non-executable"
        binary.write_text("not executable\n")
        binary.chmod(0o644)
    else:
        model = tmp_path / "missing-model"
    spawn = MagicMock()
    monkeypatch.setattr(server.subprocess, "Popen", spawn)
    config = ServerConfig(binary=binary, model=model, threads=1)
    log_path = tmp_path / "server.log"
    with pytest.raises(FileNotFoundError):
        with server.ManagedServer(config, log_path):
            pytest.fail("Missing assets must fail rather than silently skip")
    spawn.assert_not_called()
    assert not log_path.exists()


def test_memory_snapshot_records_process_rss_in_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        server.Path,
        "read_text",
        lambda _: "Name:\tllama-server\nVmPeak:\t9999 kB\nVmRSS:\t123 kB\nVmHWM:\t456 kB\n",
    )
    assert server.memory_snapshot(12345) == {"rss_bytes": 123 * 1024, "peak_rss_bytes": 456 * 1024}


def test_memory_snapshot_handles_a_process_that_has_exited(monkeypatch: pytest.MonkeyPatch) -> None:
    def gone(_: Path) -> str:
        raise FileNotFoundError("process exited")

    monkeypatch.setattr(server.Path, "read_text", gone)
    assert server.memory_snapshot(12345) == {}


def test_process_cpu_time_sums_user_and_system_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    def read_stat(path: Path) -> str:
        assert path == Path("/proc/12345/stat")
        return "12345 (llama server worker) S 1 2 3 4 5 6 7 8 9 10 120 30"

    monkeypatch.setattr(server.Path, "read_text", read_stat)
    monkeypatch.setattr(server.os, "sysconf", lambda _: 100)
    assert server.process_cpu_seconds(12345) == pytest.approx(1.5)


def test_process_cpu_time_is_unavailable_after_process_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_stat(_: Path) -> str:
        raise FileNotFoundError

    monkeypatch.setattr(server.Path, "read_text", missing_stat)
    assert server.process_cpu_seconds(12345) is None
