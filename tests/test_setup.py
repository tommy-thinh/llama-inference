import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from inference import setup


@pytest.mark.parametrize(
    "version",
    ["true (GNU coreutils) 9.7", "version: 0.7.0-dev (build 1, commit 0000000)"],
    ids=["unrelated-executable", "wrong-llama-commit"],
)
def test_verify_only_rejects_an_executable_outside_the_runtime_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture_model: Path, version: str
) -> None:
    binary = tmp_path / "llama-server"
    binary.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n")
    binary.chmod(0o755)
    monkeypatch.setenv("LLAMA_SERVER_PATH", str(binary))
    monkeypatch.setenv("LLAMA_MODEL_PATH", str(fixture_model))
    monkeypatch.setenv("LLAMA_THREADS", "1")
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["inference.setup", "--verify-only"])
    monkeypatch.setattr(setup, "verify_model", lambda _: {"sha256": "verified-model"})
    pinned_commit = setup.load_lock()["runtime"]["commit"]
    check_output = subprocess.check_output
    run = subprocess.run

    def source_or_binary(command: list[str], **kwargs: object) -> str:
        if command[0] == "git":
            return pinned_commit + "\n"
        return check_output(command, **kwargs)

    def clean_source(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 0)
        return run(command, **kwargs)

    monkeypatch.setattr(subprocess, "check_output", source_or_binary)
    monkeypatch.setattr(subprocess, "run", clean_source)
    with pytest.raises(ValueError):
        setup.main()
    assert not (tmp_path / ".runtime/artifact-verification.json").exists()


def test_setup_requires_explicit_community_license_acceptance_before_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["inference.setup"])
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Setup must stop before cloning or building"),
    )
    monkeypatch.setattr(
        setup,
        "download_model",
        lambda *args, **kwargs: pytest.fail("Setup must not download without explicit acceptance"),
    )
    with pytest.raises(SystemExit) as error:
        setup.main()
    assert error.value.code == 2


def test_setup_passes_explicit_license_acceptance_to_model_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / ".runtime/llama.cpp"
    runtime.mkdir(parents=True)
    config = SimpleNamespace(
        binary=tmp_path / "llama-server",
        model=tmp_path / "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        validate_files=lambda: None,
    )
    downloads: list[tuple[Path, bool]] = []
    lock = setup.load_lock()
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["inference.setup", "--accept-llama-license"])
    monkeypatch.setattr(setup.ServerConfig, "local", lambda: config)
    monkeypatch.setattr(setup, "check_runtime", lambda _: lock["runtime"]["commit"])
    monkeypatch.setattr(
        setup,
        "checked_runtime_version",
        lambda _: f"version: 0.7.0-dev (build 1, commit {lock['runtime']['commit'][:7]})",
    )
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0))
    monkeypatch.setattr(setup, "verify_model", lambda _: {"sha256": "fixture-sha256"})
    monkeypatch.setattr(
        setup,
        "download_model",
        lambda path, license_accepted=False: downloads.append((path, license_accepted)),
    )

    setup.main()

    assert downloads == [(config.model, True)]


def test_setup_reuses_an_existing_pinned_binary_without_rebuilding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / ".runtime/llama.cpp"
    runtime.mkdir(parents=True)
    binary = tmp_path / "llama-server"
    binary.write_text("pinned test binary\n")
    model = tmp_path / "model.gguf"
    config = SimpleNamespace(binary=binary, model=model, validate_files=lambda: None)
    downloads: list[tuple[Path, bool]] = []
    version_checks: list[Path] = []
    lock = setup.load_lock()
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["inference.setup", "--accept-llama-license", "--jobs", "1"])
    monkeypatch.setattr(setup.ServerConfig, "local", lambda: config)
    monkeypatch.setattr(setup, "check_runtime", lambda _: lock["runtime"]["commit"])
    monkeypatch.setattr(
        setup,
        "checked_runtime_version",
        lambda path: version_checks.append(path) or "pinned test runtime",
    )
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda *a, **kw: pytest.fail("A pinned existing binary must not be rebuilt"),
    )
    monkeypatch.setattr(
        setup,
        "download_model",
        lambda path, license_accepted=False: downloads.append((path, license_accepted)),
    )
    monkeypatch.setattr(setup, "verify_model", lambda _: {"sha256": "fixture-sha256"})

    setup.main()

    assert downloads == [(model, True)]
    assert version_checks == [binary, binary]


def test_setup_rejects_an_unpinned_existing_binary_before_download_or_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / ".runtime/llama.cpp"
    runtime.mkdir(parents=True)
    binary = tmp_path / "llama-server"
    binary.write_text("#!/bin/sh\nprintf '%s\\n' 'version: 0.7.0-dev (build 1, commit 0000000)'\n")
    binary.chmod(0o755)
    config = SimpleNamespace(binary=binary, model=tmp_path / "model.gguf")
    lock = setup.load_lock()
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["inference.setup", "--accept-llama-license", "--jobs", "1"])
    monkeypatch.setattr(setup.ServerConfig, "local", lambda: config)
    monkeypatch.setattr(setup, "check_runtime", lambda _: lock["runtime"]["commit"])
    monkeypatch.setattr(
        setup.subprocess,
        "check_output",
        lambda *a, **kw: "version: 0.7.0-dev (build 1, commit 0000000)\n",
    )
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda *a, **kw: pytest.fail("An existing unpinned binary must not be rebuilt"),
    )
    monkeypatch.setattr(
        setup,
        "download_model",
        lambda *a, **kw: pytest.fail("Reject the runtime before attempting model access"),
    )

    with pytest.raises(ValueError, match="pinned runtime commit"):
        setup.main()

    assert not (tmp_path / ".runtime/artifact-verification.json").exists()
