"""Reproduce the pinned CPU build and checksum-verified model download."""

import argparse
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from inference.artifacts import ROOT, load_lock, verify_model
from inference.config import ServerConfig, native_runtime_environment


def check_runtime(source: Path) -> str:
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if commit != load_lock()["runtime"]["commit"]:
        raise ValueError("Existing llama.cpp checkout differs from lock; refusing to change it")
    subprocess.run(["git", "-C", str(source), "diff", "--exit-code", "HEAD", "--"], check=True)
    return commit


def checked_runtime_version(binary: Path) -> str:
    version = subprocess.check_output(
        [str(binary), "--version"],
        text=True,
        stderr=subprocess.STDOUT,
        timeout=10,
        env=native_runtime_environment(binary),
    ).strip()
    match = re.search(
        r"^version: \S+ \(build \d+, commit ([0-9a-f]{7,40})\)", version, re.MULTILINE
    )
    if match is None or not load_lock()["runtime"]["commit"].startswith(match[1]):
        raise ValueError("llama-server executable differs from the pinned runtime commit")
    return version


def download_model(destination: Path, *, license_accepted: bool = False) -> None:
    model = load_lock()["model"]
    if destination.exists():
        verify_model(destination)
        return
    if not license_accepted:
        raise RuntimeError(
            "Model download is blocked until you accept the Llama 3.2 Community License "
            f"at {model['license_url']} and the model access terms at {model['access_url']}; "
            "then rerun setup with --accept-llama-license."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    url = (
        f"https://huggingface.co/{model['repository']}/resolve/"
        f"{model['revision']}/{model['filename']}"
    )
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, suffix=".part", delete=False
        ) as output:
            temporary = Path(output.name)
            with urllib.request.urlopen(url, timeout=60) as response:
                while chunk := response.read(4 * 1024 * 1024):
                    output.write(chunk)
        verify_model(temporary)
        temporary.rename(destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--verify-runtime-only",
        action="store_true",
        help="Verify the pinned local llama.cpp source and executable without model weights",
    )
    parser.add_argument(
        "--accept-llama-license",
        action="store_true",
        help="Attest that you personally accepted the pinned model's license and access terms",
    )
    args = parser.parse_args()
    if args.verify_only and args.verify_runtime_only:
        parser.error("--verify-only and --verify-runtime-only cannot be combined")
    if not 1 <= args.jobs <= (os.cpu_count() or 1):
        parser.error("jobs must fit the available logical CPUs")
    if not args.verify_only and not args.verify_runtime_only and not args.accept_llama_license:
        parser.error(
            "model setup requires your prior acceptance of the Llama 3.2 Community License "
            "and model access terms; review the pinned terms, then pass --accept-llama-license"
        )
    runtime = load_lock()["runtime"]
    source = ROOT / ".runtime/llama.cpp"
    logs = ROOT / ".runtime/logs"
    logs.mkdir(parents=True, exist_ok=True)
    if not (args.verify_only or args.verify_runtime_only):
        cloned_runtime = not source.exists()
        if cloned_runtime:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    runtime["tag"],
                    runtime["repository"],
                    str(source),
                ],
                check=True,
            )
        check_runtime(source)
        binary = ServerConfig.local().binary
        if binary.is_file():
            checked_runtime_version(binary)
        else:
            build_commands = [
                [
                    "cmake",
                    "-S",
                    str(source),
                    "-B",
                    str(source / "build"),
                    *runtime["cmake_options"],
                ],
                [
                    "cmake",
                    "--build",
                    str(source / "build"),
                    "--parallel",
                    str(args.jobs),
                    "--target",
                    "llama-server",
                ],
            ]
            for name, command in zip(("configure", "build"), build_commands, strict=True):
                log_path = logs / f"{name}.log"
                print(f"{name}: {log_path}", flush=True)
                with log_path.open("w") as output:
                    result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT)
                if result.returncode:
                    raise RuntimeError(f"{name} failed; see {log_path}")
        download_model(ServerConfig.local().model, license_accepted=args.accept_llama_license)
    if args.verify_runtime_only:
        commit = check_runtime(source)
        version = checked_runtime_version(ServerConfig.local().binary)
        print(f"Pinned llama.cpp runtime verified: {commit}; {version}")
        return
    config = ServerConfig.local()
    config.validate_files()
    report = {
        "verified_at": datetime.now(UTC).isoformat(),
        "runtime_commit": check_runtime(source),
        "runtime_version": checked_runtime_version(config.binary),
        "model": verify_model(config.model),
    }
    report_path = ROOT / ".runtime/artifact-verification.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"Runtime, checksum, architecture, quantization and chat template verified: {report_path}"
    )


if __name__ == "__main__":
    main()
