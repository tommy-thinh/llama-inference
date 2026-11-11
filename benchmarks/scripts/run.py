"""Run a small serial experiment with owned CPU servers and machine-readable evidence."""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from benchmarks.scripts.metrics import stream_chat, summarize
from inference.artifacts import ROOT, load_lock, sha256_file, verify_model
from inference.config import ServerConfig
from inference.server import (
    ManagedServer,
    free_local_port,
    memory_snapshot,
    process_cpu_seconds,
)
from inference.setup import check_runtime, checked_runtime_version


def read_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text())
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise ValueError("Expected a benchmark configuration with schema_version 1")
    for key in ("warmups", "repetitions"):
        if type(config.get(key)) is not int or not 1 <= config[key] <= 100:
            raise ValueError(f"{key} must be between 1 and 100")
    if not isinstance(config.get("profiles"), list) or not config["profiles"]:
        raise ValueError("profiles must be a non-empty list")
    if not isinstance(config.get("prompts"), list) or not config["prompts"]:
        raise ValueError("prompts must be a non-empty list")
    for group, identifier, required in (
        (config["profiles"], "name", {"name", "threads", "ctx_size", "max_tokens"}),
        (config["prompts"], "id", {"id", "text"}),
    ):
        seen = set()
        for item in group:
            if not isinstance(item, dict) or set(item) != required:
                raise ValueError(f"Entries must have exactly these keys: {sorted(required)}")
            name = item[identifier]
            if not isinstance(name, str) or not name or name in seen:
                raise ValueError(f"{identifier} must be a unique non-empty string")
            seen.add(name)
    for profile in config["profiles"]:
        if any(type(profile[key]) is not int for key in ("threads", "ctx_size", "max_tokens")):
            raise ValueError("Profile settings must be integers")
        ServerConfig.local(**{key: profile[key] for key in ("threads", "ctx_size", "max_tokens")})
    for prompt in config["prompts"]:
        if not isinstance(prompt["text"], str) or not prompt["text"].strip():
            raise ValueError("Prompt text must be non-empty")
    return config


def request_payload(prompt: str, max_tokens: int) -> dict[str, Any]:
    return {
        "model": "llama-3.2-1b-instruct",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "seed": 42,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "cache_prompt": False,
    }


def hardware_snapshot() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python": sys.version,
        "os_release": Path("/etc/os-release").read_text(),
        "lscpu": json.loads(subprocess.check_output(["lscpu", "--json"], text=True, timeout=10)),
        "meminfo": Path("/proc/meminfo").read_text(),
        "load_average": list(os.getloadavg()),
        "cpu_affinity": sorted(os.sched_getaffinity(0)),
        "httpx": httpx.__version__,
        "disk_usage_bytes": dict(
            zip(("total", "used", "free"), shutil.disk_usage(ROOT), strict=True)
        ),
        "compiler_version": subprocess.check_output(
            ["g++", "--version"], text=True, timeout=10
        ).strip(),
        "cmake_version": subprocess.check_output(
            ["cmake", "--version"], text=True, timeout=10
        ).strip(),
        "python_environment": {
            "conda_name": os.environ.get("CONDA_DEFAULT_ENV"),
            "conda_prefix": os.environ.get("CONDA_PREFIX"),
            "python_prefix": sys.prefix,
        },
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    report["summary"] = summarize(report["requests"])
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(path)


def run_benchmark(config_path: Path, output_path: Path) -> dict[str, Any]:
    configuration = read_config(config_path)
    base = ServerConfig.local()
    base.validate_files()
    verified = verify_model(base.model)
    runtime_commit = check_runtime(ROOT / ".runtime/llama.cpp")
    runtime_version = checked_runtime_version(base.binary)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Refuse accidental replacement of a previous experiment.
    output_path.open("x").close()
    sources = [
        *sorted((ROOT / "inference").glob("*.py")),
        *sorted((ROOT / "benchmarks/scripts").glob("*.py")),
        ROOT / "inference/artifacts.lock.json",
        ROOT / "environment.yml",
    ]
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "configuration": configuration,
        "configuration_sha256": sha256_file(config_path),
        "artifacts": load_lock(),
        "verified_model_sha256": verified["sha256"],
        "gguf": verified,
        "runtime_commit": runtime_commit,
        "runtime_version": runtime_version,
        "runtime_binary_sha256": sha256_file(base.binary),
        "cmake_cache": (ROOT / ".runtime/llama.cpp/build/CMakeCache.txt").read_text(),
        "source_sha256": {str(p.relative_to(ROOT)): sha256_file(p) for p in sources},
        "hardware": hardware_snapshot(),
        "profiles": [],
        "requests": [],
        "summary": [],
    }
    write_report(output_path, report)
    try:
        for index, profile in enumerate(configuration["profiles"]):
            name = profile["name"]
            settings = {key: profile[key] for key in ("threads", "ctx_size", "max_tokens")}
            settings["port"] = free_local_port()
            config = ServerConfig.local(**settings)
            log = ROOT / ".runtime/logs" / f"{output_path.stem}-profile-{index}.log"
            print(f"Running {name}: {settings}", flush=True)
            with (
                ManagedServer(config, log) as server,
                httpx.Client(timeout=httpx.Timeout(120, connect=5), trust_env=False) as client,
            ):
                profile_report = {
                    "name": name,
                    "settings": {
                        **asdict(config),
                        "binary": str(config.binary),
                        "model": str(config.model),
                    },
                    "command": config.command(),
                    "startup_seconds": server.startup_seconds,
                    "memory_after_startup": memory_snapshot(server.pid),
                    "server_props": client.get(config.base_url + "/props").json(),
                }
                report["profiles"].append(profile_report)
                for phase, count in (
                    ("warmup", configuration["warmups"]),
                    ("measured", configuration["repetitions"]),
                ):
                    for repeat in range(count):
                        for prompt in configuration["prompts"]:
                            payload = request_payload(prompt["text"], config.max_tokens)
                            cpu_before = process_cpu_seconds(server.pid)
                            sample = stream_chat(client, config.base_url, payload)
                            cpu_after = process_cpu_seconds(server.pid)
                            measurement = sample.to_dict()
                            if cpu_before is not None and cpu_after is not None:
                                cpu_time = max(0.0, cpu_after - cpu_before)
                                measurement["server_cpu_time_seconds"] = cpu_time
                                measurement["server_cpu_percent"] = (
                                    100 * cpu_time / sample.total_seconds
                                )
                            report["requests"].append(
                                {
                                    "profile": name,
                                    "phase": phase,
                                    "repeat": repeat,
                                    "prompt_id": prompt["id"],
                                    "payload": payload,
                                    **measurement,
                                    "memory": memory_snapshot(server.pid),
                                }
                            )
                            write_report(output_path, report)
                profile_report["memory_after_requests"] = memory_snapshot(server.pid)
                profile_report["load_average_after_requests"] = list(os.getloadavg())
            profile_report["server_exit_code"] = server.process.returncode
        report["status"] = "complete"
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        write_report(output_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "benchmarks/configs/baseline.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / "benchmarks/results/local" / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ") + ".json"
    )
    report = run_benchmark(args.config, output)
    print(f"{report['status']}: {len(report['requests'])} requests; raw results: {output}")
    for row in report["summary"]:
        speed = row["mean_runtime_decode_tokens_per_second"]
        speed_label = f"{speed:.2f}" if speed is not None else "unavailable"
        print(
            f"{row['profile']:16} {row['prompt_id']:12} "
            f"TTFT {row['median_ttft_seconds']:.3f}s; runtime decode {speed_label} tok/s"
        )


if __name__ == "__main__":
    main()
