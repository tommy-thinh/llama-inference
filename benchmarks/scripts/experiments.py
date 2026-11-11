"""Reproducible CPU optimization experiments; production defaults stay unchanged."""

import argparse
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from benchmarks.scripts.experiment_artifacts import (
    load_manifest,
    model_path,
    verify_experiment_model,
)
from benchmarks.scripts.metrics import stream_chat
from benchmarks.scripts.run import hardware_snapshot, request_payload
from inference.artifacts import ROOT, sha256_file
from inference.config import ServerConfig
from inference.server import ManagedServer, free_local_port, memory_snapshot, process_cpu_seconds
from inference.setup import check_runtime, checked_runtime_version


@dataclass(frozen=True)
class ExperimentServerConfig(ServerConfig):
    kv_type: str = "f16"
    parallel: int = 1
    batch_size: int = 256
    ubatch_size: int = 128
    flash_attn: bool = True
    cont_batching: bool = True

    def __post_init__(self):
        super().__post_init__()
        if self.kv_type not in {"f16", "q8_0", "q4_0"}:
            raise ValueError("Unsupported experimental KV type")
        if type(self.parallel) is not int or self.parallel not in {1, 2, 4}:
            raise ValueError("Experimental slots must be 1, 2, or 4")
        if any(type(v) is not int for v in (self.batch_size, self.ubatch_size)) or not (
            1 <= self.ubatch_size <= self.batch_size <= 2048
        ):
            raise ValueError("Require 1 <= ubatch <= batch <= 2048")
        if type(self.flash_attn) is not bool or type(self.cont_batching) is not bool:
            raise ValueError("Flash attention and continuous batching must be booleans")
        if self.kv_type != "f16" and not self.flash_attn:
            raise ValueError("Quantized V cache requires flash attention in this runtime")

    def command(self):
        command = super().command()
        for flag, value in {
            "--ctx-size": self.ctx_size * self.parallel,
            "--parallel": self.parallel,
            "--batch-size": self.batch_size,
            "--ubatch-size": self.ubatch_size,
            "--cache-type-k": self.kv_type,
            "--cache-type-v": self.kv_type,
            "--flash-attn": "on" if self.flash_attn else "off",
        }.items():
            command[command.index(flag) + 1] = str(value)
        command.append("--cont-batching" if self.cont_batching else "--no-cont-batching")
        return command


DEFAULTS = {
    "model": "q4",
    "threads": 4,
    "ctx_size": 2048,
    "max_tokens": 32,
    "kv_type": "f16",
    "parallel": 1,
    "batch_size": 256,
    "ubatch_size": 128,
    "flash_attn": True,
    "cont_batching": True,
    "cache_prompt": False,
    "clients": 1,
}


def read_config(path):
    config = json.loads(Path(path).read_text())
    if (
        not isinstance(config, dict)
        or set(config)
        != {"schema_version", "warmups", "repetitions", "profiles", "prompts", "quality"}
        or type(config["schema_version"]) is not int
        or config["schema_version"] != 1
    ):
        raise ValueError("Expected complete Phase 5 schema_version1 configuration")
    for key, lower in (("warmups", 1), ("repetitions", 3)):
        if type(config[key]) is not int or not lower <= config[key] <= 10:
            raise ValueError(f"Invalid {key}")
    for group in ("profiles", "prompts", "quality"):
        if not isinstance(config[group], list) or not config[group]:
            raise ValueError(f"Require nonempty {group}")
        names = [
            row.get("name" if group == "profiles" else "id")
            for row in config[group]
            if isinstance(row, dict)
        ]
        if (
            len(names) != len(config[group])
            or any(not isinstance(n, str) or not n for n in names)
            or len(set(names)) != len(names)
        ):
            raise ValueError(f"Require unique nonempty identifiers in {group}")
    normalized = []
    for row in config["profiles"]:
        if set(row) - {"name", *DEFAULTS}:
            raise ValueError("Unknown profile keys")
        selected = {**DEFAULTS, **row}
        if (
            selected["model"] not in {"q4", "q8"}
            or type(selected["clients"]) is not int
            or selected["clients"] not in {1, 2, 4}
        ):
            raise ValueError("Invalid model or client count")
        if type(selected["cache_prompt"]) is not bool:
            raise ValueError("cache_prompt must be boolean")
        for name in ("threads", "ctx_size", "max_tokens"):
            if type(selected[name]) is not int:
                raise ValueError("Profile numeric fields must be integers")
        ExperimentServerConfig(**server_values(selected))
        normalized.append(selected)
    config["profiles"] = normalized
    for row in config["prompts"]:
        if (
            set(row) != {"id", "text"}
            or not isinstance(row["text"], str)
            or not row["text"].strip()
        ):
            raise ValueError("Prompts require id and nonblank text")
    for row in config["quality"]:
        if (
            set(row) != {"id", "prompt", "pattern"}
            or not isinstance(row["prompt"], str)
            or not row["prompt"].strip()
        ):
            raise ValueError("Quality cases require id, prompt, pattern")
        re.compile(row["pattern"])
    return config


def server_values(profile):
    base = ServerConfig.local()
    return {
        "binary": base.binary,
        "model": model_path(profile["model"]),
        **{
            k: profile[k]
            for k in (
                "threads",
                "ctx_size",
                "max_tokens",
                "kv_type",
                "parallel",
                "batch_size",
                "ubatch_size",
                "flash_attn",
                "cont_batching",
            )
        },
    }


def write_report(path, report):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def cache_allocation(log_path):
    text = log_path.read_text(errors="replace")
    lines = [
        line
        for line in text.splitlines()
        if re.search(r"KV.*(buffer size|size =)|K \(.*\).*V \(", line)
    ]
    return {
        "runtime_log_lines": lines,
        "buffer_mib": [float(v) for v in re.findall(r"KV buffer size\s*=\s*([0-9.]+) MiB", text)],
    }


def run(config_path, output_path):
    config = read_config(config_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x"):
        pass
    report = {
        "schema_version": 2,
        "kind": "native",
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "configuration": config,
        "configuration_sha256": sha256_file(Path(config_path)),
        "models": {},
        "profiles": [],
        "requests": [],
        "waves": [],
        "quality": [],
    }
    write_report(output_path, report)
    try:
        report.update(
            {
                "runtime_commit": check_runtime(ROOT / ".runtime/llama.cpp"),
                "runtime_version": checked_runtime_version(ServerConfig.local().binary),
                "runtime_binary_sha256": sha256_file(ServerConfig.local().binary),
                "hardware": hardware_snapshot(),
                "artifact_manifest": load_manifest(),
                "source_sha256": {
                    str(p.relative_to(ROOT)): sha256_file(p)
                    for p in sorted((ROOT / "benchmarks/scripts").glob("*.py"))
                },
            }
        )
        for model_id in sorted({p["model"] for p in config["profiles"]}):
            report["models"][model_id] = verify_experiment_model(model_path(model_id), model_id)
        quality_done = set()
        for index, profile in enumerate(config["profiles"]):
            native = ExperimentServerConfig(**server_values(profile), port=free_local_port())
            log = ROOT / ".runtime/logs" / f"phase5-{output_path.stem}-{index}.log"
            entry = {
                "name": profile["name"],
                "command": native.command(),
                "settings": {
                    **asdict(native),
                    "model": str(native.model),
                    "binary": str(native.binary),
                },
                "status": "starting",
            }
            report["profiles"].append(entry)
            print(f"Running {profile['name']}", flush=True)
            server = ManagedServer(native, log, startup_timeout=60)
            try:
                with server:
                    entry.update(
                        startup_seconds=server.startup_seconds,
                        memory_after_startup=memory_snapshot(server.pid),
                    )
                    with httpx.Client(
                        timeout=httpx.Timeout(120, connect=5), trust_env=False
                    ) as client:
                        entry["server_props"] = client.get(native.base_url + "/props").json()
                        for prompt in config["prompts"]:
                            for phase, count in (
                                ("warmup", config["warmups"]),
                                ("measured", config["repetitions"]),
                            ):
                                for repeat in range(count):
                                    payload = request_payload(prompt["text"], native.max_tokens)
                                    # Begin each prompt's warm-up from a full prefill,
                                    # even when different prompts share chat headers.
                                    payload["cache_prompt"] = profile["cache_prompt"] and not (
                                        phase == "warmup" and repeat == 0
                                    )
                                    barrier = threading.Barrier(profile["clients"])

                                    def request_one(
                                        _,
                                        barrier=barrier,
                                        client=client,
                                        base_url=native.base_url,
                                        payload=payload,
                                    ):
                                        barrier.wait(timeout=10)
                                        try:
                                            return {
                                                "success": True,
                                                **stream_chat(client, base_url, payload).to_dict(),
                                            }
                                        except (
                                            httpx.HTTPError,
                                            ValueError,
                                            TypeError,
                                            AttributeError,
                                        ) as exc:
                                            return {
                                                "success": False,
                                                "error": type(exc).__name__,
                                                **getattr(exc, "partial_measurement", {}),
                                            }

                                    cpu_before = process_cpu_seconds(server.pid)
                                    started = time.perf_counter()
                                    with ThreadPoolExecutor(
                                        max_workers=profile["clients"]
                                    ) as executor:
                                        samples = list(
                                            executor.map(request_one, range(profile["clients"]))
                                        )
                                    elapsed = time.perf_counter() - started
                                    cpu_after = process_cpu_seconds(server.pid)
                                    wave_id = len(report["waves"])
                                    common = {
                                        "profile": profile["name"],
                                        "prompt_id": prompt["id"],
                                        "clients": profile["clients"],
                                        "phase": phase,
                                        "repeat": repeat,
                                        "wave_id": wave_id,
                                    }
                                    report["waves"].append(
                                        {
                                            **common,
                                            "wall_seconds": elapsed,
                                            "server_cpu_seconds_before": cpu_before,
                                            "server_cpu_seconds_after": cpu_after,
                                            "server_cpu_time_seconds": max(
                                                0, cpu_after - cpu_before
                                            )
                                            if cpu_before is not None and cpu_after is not None
                                            else None,
                                            "server_cpu_percent": 100
                                            * max(0, cpu_after - cpu_before)
                                            / elapsed
                                            if cpu_before is not None and cpu_after is not None
                                            else None,
                                            "memory": memory_snapshot(server.pid),
                                        }
                                    )
                                    report["requests"].extend(
                                        {**common, "payload": payload, **sample}
                                        for sample in samples
                                    )
                                    write_report(output_path, report)
                        if profile["model"] not in quality_done:
                            for case in config["quality"]:
                                payload = request_payload(case["prompt"], 32)
                                sample = stream_chat(client, native.base_url, payload).to_dict()
                                report["quality"].append(
                                    {
                                        "model": profile["model"],
                                        **case,
                                        "payload": payload,
                                        **sample,
                                        "passed": bool(
                                            re.fullmatch(case["pattern"], sample["output"].strip())
                                        ),
                                    }
                                )
                                write_report(output_path, report)
                            quality_done.add(profile["model"])
                    entry["memory_after_requests"] = memory_snapshot(server.pid)
                    entry["cache_allocation"] = cache_allocation(log)
                    entry["status"] = "complete"
            except (RuntimeError, TimeoutError, httpx.HTTPError, ValueError) as exc:
                entry["status"] = "failed"
                entry["error"] = str(exc)
                entry["runtime_log_tail"] = (
                    log.read_text(errors="replace")[-5000:] if log.exists() else ""
                )
            finally:
                entry["server_exit_code"] = server.process.returncode if server.process else None
                write_report(output_path, report)
        report["status"] = (
            "complete"
            if all(p["status"] == "complete" for p in report["profiles"])
            and all(r["success"] for r in report["requests"])
            else "complete_with_failures"
        )
    except BaseException as exc:
        report["status"] = "interrupted"
        report["error"] = type(exc).__name__
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        write_report(output_path, report)
    return report


@dataclass(frozen=True)
class AllocationConfig(ExperimentServerConfig):
    def command(self):
        # Library allocation INFO maps to TRACE (4) in this pinned server.
        return [*super().command(), "--log-verbosity", "4"]


def probe_cache(output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x"):
        pass
    report = {
        "schema_version": 1,
        "kind": "cache_allocation",
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "profiles": [],
    }
    write_report(output_path, report)
    try:
        report["model"] = verify_experiment_model(model_path("q4"), "q4")
        report["runtime_commit"] = check_runtime(ROOT / ".runtime/llama.cpp")
        choices = [
            ("f16", 1024, 1),
            ("f16", 2048, 1),
            ("f16", 4096, 1),
            ("q8_0", 2048, 1),
            ("q4_0", 2048, 1),
            ("f16", 2048, 4),
        ]
        for index, (kind, context, parallel) in enumerate(choices):
            config = AllocationConfig(
                **server_values(
                    {**DEFAULTS, "kv_type": kind, "ctx_size": context, "parallel": parallel}
                ),
                port=free_local_port(),
            )
            log = ROOT / ".runtime/logs" / f"{output_path.stem}-{index}.log"
            server = ManagedServer(config, log, startup_timeout=60)
            row = {
                "kv_type": kind,
                "context_per_slot": context,
                "parallel": parallel,
                "command": config.command(),
            }
            report["profiles"].append(row)
            with server:
                row["memory_after_startup"] = memory_snapshot(server.pid)
                row["allocation"] = cache_allocation(log)
                if not row["allocation"]["buffer_mib"]:
                    raise ValueError("Runtime did not report a KV allocation")
            row["server_exit_code"] = server.process.returncode
            write_report(output_path, report)
        report["status"] = "complete"
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = type(exc).__name__
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        write_report(output_path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "benchmarks/configs/phase5-native.json"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--probe-cache", action="store_true")
    args = parser.parse_args()
    path = args.output or ROOT / "benchmarks/results/local" / (
        "phase5-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ") + ".json"
    )
    report = probe_cache(path) if args.probe_cache else run(args.config, path)
    print(f"{report['status']}: {path}")
    if report["status"] != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
