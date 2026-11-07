"""Foreground contracts: verified identities and CLI precedence before exec."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from test_artifacts import model_bytes

from inference import artifacts, server


@pytest.fixture
def foreground_assets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path, MagicMock]:
    """Synthetic pinned GGUF; native verification commands are mocked, never served."""
    lock = artifacts.load_lock()
    content = model_bytes()
    lock["model"]["size_bytes"] = len(content)
    lock["model"]["sha256"] = hashlib.sha256(content).hexdigest()
    lock_path = tmp_path / "artifacts.lock.json"
    lock_path.write_text(json.dumps(lock))
    monkeypatch.setattr(artifacts, "LOCK_PATH", lock_path)
    model = tmp_path / "verified-external-model.gguf"
    model.write_bytes(content)
    binary = tmp_path / "external-runtime" / "llama-server"
    binary.parent.mkdir()
    binary.write_text("not executed: native process boundaries are mocked\n")
    binary.chmod(0o755)
    monkeypatch.setenv("LLAMA_MODEL_PATH", str(model))
    monkeypatch.setenv("LLAMA_SERVER_PATH", str(binary))
    for name in ("LLAMA_THREADS", "LLAMA_CTX_SIZE", "LLAMA_MAX_TOKENS", "LLAMA_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["inference.server", "--threads", "1"])

    def version_or_source(command: list[str], **kwargs: object) -> str:
        if command[0] == "git":
            assert command[-2:] == ["rev-parse", "HEAD"]
            return lock["runtime"]["commit"] + "\n"
        assert command == [str(binary), "--version"]
        return f"version: 0.7.0-dev (build 1, commit {lock['runtime']['commit'][:7]})\n"

    def source_clean(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        assert command[0] == "git"
        assert command[-4:] == ["diff", "--exit-code", "HEAD", "--"]
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "check_output", version_or_source)
    monkeypatch.setattr(subprocess, "run", source_clean)
    execute = MagicMock(side_effect=SystemExit("mock exec boundary"))
    monkeypatch.setattr(server.os, "execve", execute)
    return model, binary, execute


@pytest.mark.parametrize("corruption", ["different-size", "different-checksum"])
def test_foreground_rejects_unverified_model_before_exec(
    foreground_assets: tuple[Path, Path, MagicMock], corruption: str
) -> None:
    model, _, execute = foreground_assets
    original = model.read_bytes()
    model.write_bytes(
        b"historical Qwen placeholder" if corruption == "different-size" else b"X" + original[1:]
    )
    with pytest.raises(ValueError, match="size|SHA-256"):
        server.main()
    execute.assert_not_called()


@pytest.mark.parametrize("identity", ["wrong-source", "wrong-executable", "unrelated-executable"])
def test_foreground_rejects_unpinned_runtime_before_exec(
    monkeypatch: pytest.MonkeyPatch,
    foreground_assets: tuple[Path, Path, MagicMock],
    identity: str,
) -> None:
    _, binary, execute = foreground_assets
    pinned_commit = artifacts.load_lock()["runtime"]["commit"]

    def runtime_identity(command: list[str], **kwargs: object) -> str:
        if command[0] == "git":
            return "0" * 40 if identity == "wrong-source" else pinned_commit
        assert command == [str(binary), "--version"]
        if identity == "unrelated-executable":
            return "true (GNU coreutils) 9.7\n"
        commit = "0000000" if identity == "wrong-executable" else pinned_commit[:7]
        return f"version: 0.7.0-dev (build 1, commit {commit})\n"

    monkeypatch.setattr(subprocess, "check_output", runtime_identity)
    with pytest.raises(ValueError, match="checkout differs|pinned runtime commit"):
        server.main()
    execute.assert_not_called()


def test_foreground_verified_alternate_paths_reach_exec_with_local_libraries(
    monkeypatch: pytest.MonkeyPatch,
    foreground_assets: tuple[Path, Path, MagicMock],
) -> None:
    model, binary, execute = foreground_assets
    monkeypatch.setenv("LLAMA_ARG_HOST", "0.0.0.0")
    monkeypatch.setenv("LLAMA_API_KEY", "inherited-key")
    with pytest.raises(SystemExit, match="mock exec boundary"):
        server.main()
    executable, command, environment = execute.call_args.args
    assert executable == str(binary)
    assert command[command.index("--model") + 1] == str(model)
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert environment["LD_LIBRARY_PATH"].split(":")[0] == str(binary.parent)
    assert "LLAMA_ARG_HOST" not in environment
    assert "LLAMA_API_KEY" not in environment


@pytest.mark.parametrize("inherited", ["invalid", "0"])
def test_foreground_explicit_settings_override_invalid_inherited_values(
    monkeypatch: pytest.MonkeyPatch,
    foreground_assets: tuple[Path, Path, MagicMock],
    inherited: str,
) -> None:
    _, _, execute = foreground_assets
    for name in ("LLAMA_THREADS", "LLAMA_CTX_SIZE", "LLAMA_MAX_TOKENS", "LLAMA_PORT"):
        monkeypatch.setenv(name, inherited)
    settings = {"--threads": "1", "--ctx-size": "1024", "--max-tokens": "16", "--port": "8082"}
    monkeypatch.setattr(
        sys, "argv", ["inference.server", *[item for pair in settings.items() for item in pair]]
    )
    with pytest.raises(SystemExit, match="mock exec boundary"):
        server.main()
    command = execute.call_args.args[1]
    for flag, value in settings.items():
        runtime_flag = "--n-predict" if flag == "--max-tokens" else flag
        assert command[command.index(runtime_flag) + 1] == value
