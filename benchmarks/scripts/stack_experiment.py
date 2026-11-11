"""Measure the real public profile over private, certificate-verified loopback HTTPS."""

import argparse
import json
import math
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from statistics import mean, median

import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from benchmarks.scripts.run import hardware_snapshot
from inference.artifacts import ROOT, load_lock, sha256_file, verify_model
from inference.config import ServerConfig
from inference.server import ManagedServer, free_local_port, memory_snapshot, process_cpu_seconds
from inference.setup import check_runtime, checked_runtime_version


def read_config(path: Path) -> dict:
    config = json.loads(path.read_text())
    keys = {
        "schema_version",
        "warmups",
        "repetitions",
        "clients",
        "strategies",
        "max_tokens",
        "prompt",
    }
    if not isinstance(config, dict) or set(config) != keys:
        raise ValueError("Stack configuration has missing or unknown keys")
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ValueError("Expected schema_version 1")
    for name, minimum in (("warmups", 1), ("repetitions", 3)):
        if type(config[name]) is not int or not minimum <= config[name] <= 100:
            raise ValueError(f"{name} must be an integer between {minimum} and 100")
    if config["clients"] != [1, 2, 4] or any(type(n) is not int for n in config["clients"]):
        raise ValueError("clients must be [1, 2, 4]")
    if config["strategies"] != {"fifo": 2, "reject": 0} or any(
        type(n) is not int for n in config["strategies"].values()
    ):
        raise ValueError("strategies must be {fifo: 2, reject: 0}")
    if type(config["max_tokens"]) is not int or config["max_tokens"] != 32:
        raise ValueError("The controlled stack comparison requires max_tokens 32")
    if not isinstance(config["prompt"], str) or not 1 <= len(config["prompt"].strip()) <= 4096:
        raise ValueError("prompt must be non-empty and at most 4096 characters")
    return config


def distribution(values: list[float]) -> dict:
    if not values:
        return {"mean": None, "median": None, "p95": None}
    return {
        "mean": mean(values),
        "median": median(values),
        "p95": sorted(values)[math.ceil(len(values) * 0.95) - 1],
    }


def summarize(waves: list[dict]) -> list[dict]:
    groups = {}
    for wave in waves:
        if wave["phase"] == "measured" and wave.get("attempts") and wave.get("wall_seconds"):
            groups.setdefault((wave["strategy"], wave["clients"]), []).append(wave)
    result = []
    for (strategy, clients), group in groups.items():
        attempts = [row for wave in group for row in wave["attempts"]]
        successes = [row for row in attempts if row["success"]]
        rejected = sum(row.get("rejected", False) for row in attempts)
        wall = sum(wave["wall_seconds"] for wave in group)
        result.append(
            {
                "strategy": strategy,
                "clients": clients,
                "waves": len(group),
                "attempts": len(attempts),
                "successes": len(successes),
                "rejections": rejected,
                "errors": len(attempts) - len(successes) - rejected,
                "rejection_rate": rejected / len(attempts) if attempts else None,
                "wall_seconds": wall,
                "successful_requests_per_second": len(successes) / wall if wall else None,
                "completion_tokens_per_second": sum(
                    row["usage"].get("completion_tokens", 0) for row in successes
                )
                / wall
                if wall
                else None,
                "attempt_latency_seconds": distribution([row["total_seconds"] for row in attempts]),
                "successful_latency_seconds": distribution(
                    [row["total_seconds"] for row in successes]
                ),
                "ttft_seconds": distribution([row["ttft_seconds"] for row in successes]),
                "queue_wait_seconds": distribution(
                    [row["queue_wait_seconds"] for row in successes]
                ),
            }
        )
    return result


def stream_attempt(client, path, payload, barrier) -> dict:
    """Keep every received SSE frame, including errors and incomplete responses."""
    row = {
        "path": path,
        "payload": payload,
        "success": False,
        "rejected": False,
        "http_status": None,
        "ttft_seconds": None,
        "queue_wait_seconds": None,
        "output": "",
        "usage": {},
        "timings": {},
        "metadata": {},
        "frames": [],
        "done": False,
        "finish_reason": None,
    }
    started = time.perf_counter()
    row["started_at"] = datetime.now(UTC).isoformat()
    try:
        barrier.wait(timeout=15)
        started = time.perf_counter()
        with client.stream("POST", path + "/chat", json=payload) as response:
            row["http_status"] = response.status_code
            if response.status_code != 200:
                row["error_response"] = response.read().decode(errors="replace")
                try:
                    detail = json.loads(row["error_response"]).get("detail")
                except (ValueError, AttributeError):
                    detail = None
                row["rejected"] = (
                    response.status_code == 503
                    and "Retry-After" in response.headers
                    and detail in ("Inference queue is full", "Inference queue wait expired")
                )
                row["error"] = "http_error"
            else:
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    raise ValueError("Expected text/event-stream")
                lines = []
                for line in response.iter_lines():
                    if line:
                        lines.append(line)
                        continue
                    if not lines:
                        continue
                    frame = "\n".join(lines) + "\n\n"
                    row["frames"].append(frame)
                    data = "\n".join(
                        value[5:].removeprefix(" ") for value in lines if value.startswith("data:")
                    )
                    lines = []
                    if not data:
                        continue
                    if data == "[DONE]":
                        row["done"] = True
                        break
                    packet = json.loads(data)
                    if not isinstance(packet, dict):
                        raise ValueError("Expected object SSE data")
                    if "request_id" in packet and "conversation_id" in packet:
                        row["metadata"].update(packet)
                        row["queue_wait_seconds"] = packet.get("queue_wait_seconds")
                    if "error" in packet:
                        row["error"] = packet["error"]
                    row["usage"].update(packet.get("usage") or {})
                    row["timings"].update(packet.get("timings") or {})
                    for choice in packet.get("choices", []):
                        content = (choice.get("delta") or {}).get("content")
                        if content:
                            if row["ttft_seconds"] is None:
                                row["ttft_seconds"] = time.perf_counter() - started
                            row["output"] += content
                        if choice.get("finish_reason") is not None:
                            row["finish_reason"] = choice["finish_reason"]
                if lines:
                    row["frames"].append("\n".join(lines))
                tokens = row["usage"].get("completion_tokens")
                prompt_tokens = row["usage"].get("prompt_tokens")
                wait = row["queue_wait_seconds"]
                row["success"] = bool(
                    row["done"]
                    and row["output"]
                    and row["finish_reason"] is not None
                    and "error" not in row
                    and type(tokens) is int
                    and 0 < tokens <= payload["max_tokens"]
                    and type(prompt_tokens) is int
                    and prompt_tokens > 0
                    and type(wait) in (float, int)
                    and math.isfinite(wait)
                    and wait >= 0
                )
                if not row["success"]:
                    row.setdefault("error", "incomplete_or_invalid_stream")
    except Exception as exc:
        # Exception text may contain URLs or credentials; record only the exception type.
        row["error"] = type(exc).__name__
    row["finished_monotonic"] = time.perf_counter()
    row["total_seconds"] = row["finished_monotonic"] - started
    row["finished_at"] = datetime.now(UTC).isoformat()
    return row


@contextmanager
def isolated_database():
    raw_url = os.environ.get("LLAMA_TEST_DATABASE_URL")
    if not raw_url:
        raise ValueError("Export LLAMA_TEST_DATABASE_URL for a dedicated loopback *_test database")
    url = make_url(raw_url)
    if (
        url.drivername != "postgresql+psycopg"
        or not (url.database or "").endswith("_test")
        or url.host not in {"127.0.0.1", "localhost", "::1"}
    ):
        raise ValueError("Refusing database outside loopback PostgreSQL+psycopg *_test")
    schema = "benchmark_" + uuid.uuid4().hex
    admin = create_engine(url, poolclass=NullPool, hide_parameters=True)
    created = False
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        isolated = url.update_query_dict({"options": f"-csearch_path={schema}"})
        engine = create_engine(isolated, poolclass=NullPool, hide_parameters=True)
        try:
            migration = Config(str(ROOT / "alembic.ini"))
            migration.set_main_option("script_location", str(ROOT / "backend/migrations"))
            with engine.begin() as connection:
                migration.attributes["connection"] = connection
                command.upgrade(migration, "head")
            yield isolated, schema
        finally:
            engine.dispose()
    finally:
        if created:
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@contextmanager
def managed_process(command_line, environment, log_path, record):
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            command_line, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT
        )
        record.update({"command": command_line, "pid": process.pid, "log_path": str(log_path)})
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            record["exit_code"] = process.returncode


def wait_ready(client, *processes):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError("Owned stack process exited before readiness")
        try:
            if client.get("/ready").status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.05)
    raise TimeoutError("Owned HTTPS stack did not become ready within 20 seconds")


def snapshot(processes):
    return {
        name: {"pid": pid, "cpu_seconds": process_cpu_seconds(pid), **memory_snapshot(pid)}
        for name, pid in processes.items()
    }


def measure_wave(client, payload, clients, processes, result=None, progress=None):
    wave = result if result is not None else {}
    wave.update({"attempts": [], "wall_seconds": 0.0})
    paths = []
    for _ in range(clients):
        response = client.post("/api/conversations", json={"title": "Phase 5 private experiment"})
        response.raise_for_status()
        paths.append("/api/conversations/" + response.json()["id"])
    barrier = threading.Barrier(clients + 1)
    stop = threading.Event()
    samples = []

    def sample_resources():
        while not stop.wait(0.05):
            samples.append(
                {"elapsed_seconds": time.perf_counter() - started, "processes": snapshot(processes)}
            )

    before = snapshot(processes)
    wave.update({"resources_before": before, "resource_samples": samples})
    with ThreadPoolExecutor(max_workers=clients) as executor:
        futures = [
            executor.submit(stream_attempt, client, path, payload, barrier) for path in paths
        ]
        started = time.perf_counter()
        sampler = threading.Thread(target=sample_resources, daemon=True)
        sampler.start()
        try:
            barrier.wait(timeout=15)
            for future in as_completed(futures, timeout=150):
                wave["attempts"].append(future.result())
                wave["wall_seconds"] = (
                    max(row["finished_monotonic"] for row in wave["attempts"]) - started
                )
                if progress is not None:
                    progress()
            wall = wave["wall_seconds"]
        finally:
            stop.set()
            sampler.join(timeout=2)
    after = snapshot(processes)
    wave.update(
        {
            "wall_seconds": wall,
            "resources_before": before,
            "resources_after": after,
            "resource_samples": samples,
        }
    )
    for name in processes:
        first, last = before[name]["cpu_seconds"], after[name]["cpu_seconds"]
        if first is not None and last is not None:
            after[name]["wave_cpu_seconds"] = max(0, last - first)
            after[name]["wave_cpu_percent"] = 100 * max(0, last - first) / wall
    # Persistence inspection happens after timed generation and uses the same cookie owner.
    for row in wave["attempts"]:
        try:
            response = client.get(row["path"] + "/requests")
            response.raise_for_status()
            row["persisted_requests"] = response.json()["items"]
        except Exception as exc:
            row["persistence_inspection_error"] = type(exc).__name__
    return wave


def write_report(path, report):
    report["summary"] = summarize(report["waves"])
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(path)


def run(config_path: Path, output_path: Path) -> dict:
    config_path, output_path = Path(config_path), Path(output_path)
    configuration = read_config(config_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.open("x").close()
    report = {
        "schema_version": 1,
        "kind": "public_https_stack",
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "configuration": configuration,
        "configuration_sha256": sha256_file(config_path),
        "waves": [],
        "processes": [],
    }
    write_report(output_path, report)
    native = None
    try:
        base = ServerConfig.local(threads=4, ctx_size=4096, max_tokens=32, port=free_local_port())
        verified = verify_model(base.model)
        report.update(
            {
                "artifacts": load_lock(),
                "verified_model": verified,
                "runtime_commit": check_runtime(ROOT / ".runtime/llama.cpp"),
                "runtime_version": checked_runtime_version(base.binary),
                "runtime_binary_sha256": sha256_file(base.binary),
                "hardware": hardware_snapshot(),
                "software": {
                    name: version(name)
                    for name in ("httpx", "fastapi", "uvicorn", "SQLAlchemy", "alembic", "psycopg")
                },
            }
        )
        sources = [
            *sorted((ROOT / "backend").rglob("*.py")),
            *sorted((ROOT / "inference").glob("*.py")),
            *sorted((ROOT / "benchmarks/scripts").glob("*.py")),
            ROOT / "deploy/Caddyfile",
            ROOT / "environment.yml",
            ROOT / "inference/artifacts.lock.json",
        ]
        report["source_sha256"] = {
            str(path.relative_to(ROOT)): sha256_file(path) for path in sources
        }
        staged = ROOT / ".runtime/caddy-test/extracted/usr/bin/caddy"
        caddy = (
            os.getenv("LLAMA_TEST_CADDY")
            or shutil.which("caddy")
            or (str(staged) if staged.is_file() else None)
        )
        openssl = shutil.which("openssl")
        if not caddy or not openssl:
            raise RuntimeError("A staged Caddy binary and OpenSSL are required")
        report["caddy"] = {
            "binary": caddy,
            "sha256": sha256_file(Path(caddy)),
            "version": subprocess.check_output([caddy, "version"], text=True, timeout=10).strip(),
        }
        logs = ROOT / ".runtime/logs" / ("stack-" + uuid.uuid4().hex)
        logs.mkdir(parents=True)
        native_record = {
            "kind": "native",
            "command": base.command(),
            "log_path": str(logs / "native.log"),
        }
        report["processes"].append(native_record)
        native = ManagedServer(base, logs / "native.log")
        with (
            isolated_database() as (database, schema),
            tempfile.TemporaryDirectory(prefix="llama-stack-") as temporary,
            native as server,
        ):
            native_record.update({"pid": server.pid, "startup_seconds": server.startup_seconds})
            report["database"] = {
                "dedicated_test_database": True,
                "schema": schema,
                "migrated_to": "head",
            }
            private = Path(temporary)
            certificate, key = private / "loopback.crt", private / "loopback.key"
            subprocess.run(
                [
                    openssl,
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-days",
                    "1",
                    "-subj",
                    "/CN=127.0.0.1",
                    "-addext",
                    "subjectAltName=IP:127.0.0.1",
                    "-keyout",
                    str(key),
                    "-out",
                    str(certificate),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            key.chmod(0o600)
            backend_port, proxy_port = free_local_port(), free_local_port()
            origin = f"https://127.0.0.1:{proxy_port}"
            environment = {
                key: value for key, value in os.environ.items() if not key.startswith("LLAMA_")
            }
            environment.update(
                {
                    "LLAMA_PUBLIC_ORIGIN": origin,
                    "LLAMA_SESSION_COOKIE_SECURE": "true",
                    "LLAMA_DATABASE_URL": database.render_as_string(hide_password=False),
                    "LLAMA_API_UPSTREAM_URL": base.base_url,
                    "LLAMA_STATIC_ROOT": str(ROOT / "frontend"),
                    "LLAMA_LEGAL_ROOT": str(ROOT / "legal"),
                    "LLAMA_ACME_EMAIL": "loopback-test@example.invalid",
                    "XDG_DATA_HOME": str(private / "caddy-data"),
                    "XDG_CONFIG_HOME": str(private / "caddy-config"),
                    "LLAMA_QUEUE_WAIT_TIMEOUT": "60",
                }
            )
            limits = {
                "LLAMA_" + name: "10000000"
                for name in (
                    "IP_REQUESTS_PER_MINUTE",
                    "SESSION_REQUESTS_PER_MINUTE",
                    "DAILY_SESSION_REQUESTS",
                    "DAILY_SESSION_TOKENS",
                    "DAILY_IP_REQUESTS",
                    "DAILY_IP_TOKENS",
                )
            }
            environment.update(limits)
            report["public_profile"] = {
                "origin": origin,
                "secure_cookie": True,
                "queue_wait_timeout": 60,
                "test_only_limits": limits,
                "tls": "temporary self-signed IP SAN trusted only by benchmark client",
            }
            source = (ROOT / "deploy/Caddyfile").read_text()
            if not source.startswith("{\n") or source.count("{$LLAMA_DOMAIN} {") != 1:
                raise ValueError("Unexpected deployment Caddyfile shape")
            source = (
                source.replace("{\n", "{\n\tauto_https off\n", 1)
                .replace("admin 127.0.0.1:2019", "admin off")
                .replace(
                    "{$LLAMA_DOMAIN} {", f"{origin} {{\n\tbind 127.0.0.1\n\ttls {certificate} {key}"
                )
                .replace("127.0.0.1:8000", f"127.0.0.1:{backend_port}")
            )
            caddy_config = private / "Caddyfile"
            caddy_config.write_text(source)
            report["caddy_configuration"] = source
            subprocess.run(
                [caddy, "validate", "--config", str(caddy_config), "--adapter", "caddyfile"],
                env=environment,
                check=True,
                capture_output=True,
                timeout=10,
            )
            tls = ssl.create_default_context(cafile=str(certificate))
            caddy_record = {"kind": "caddy"}
            report["processes"].append(caddy_record)
            with (
                managed_process(
                    [caddy, "run", "--config", str(caddy_config), "--adapter", "caddyfile"],
                    environment,
                    logs / "caddy.log",
                    caddy_record,
                ) as proxy,
                httpx.Client(
                    base_url=origin,
                    verify=tls,
                    trust_env=False,
                    timeout=httpx.Timeout(130, connect=5),
                    headers={"Origin": origin},
                ) as client,
            ):
                payload = {
                    "content": configuration["prompt"],
                    "max_tokens": configuration["max_tokens"],
                    "temperature": 0,
                    "seed": 42,
                }
                bootstrapped = False
                for strategy, queue_size in configuration["strategies"].items():
                    backend_record = {
                        "kind": "backend",
                        "strategy": strategy,
                        "queue_size": queue_size,
                    }
                    report["processes"].append(backend_record)
                    environment["LLAMA_QUEUE_SIZE"] = str(queue_size)
                    with managed_process(
                        [sys.executable, "-m", "backend", "--port", str(backend_port)],
                        environment,
                        logs / f"backend-{strategy}.log",
                        backend_record,
                    ) as backend:
                        wait_ready(client, backend, proxy)
                        if not bootstrapped:
                            response = client.post("/api/session")
                            response.raise_for_status()
                            if "secure" not in response.headers.get("set-cookie", "").lower():
                                raise RuntimeError("Public session cookie was not Secure")
                            bootstrapped = True
                        for clients in configuration["clients"]:
                            for phase, count in (
                                ("warmup", configuration["warmups"]),
                                ("measured", configuration["repetitions"]),
                            ):
                                for repeat in range(count):
                                    wave = {
                                        "strategy": strategy,
                                        "queue_size": queue_size,
                                        "clients": clients,
                                        "phase": phase,
                                        "repeat": repeat,
                                        "started_at": datetime.now(UTC).isoformat(),
                                    }
                                    report["waves"].append(wave)
                                    wave.update(
                                        measure_wave(
                                            client,
                                            payload,
                                            clients,
                                            {
                                                "native": server.pid,
                                                "backend": backend.pid,
                                                "caddy": proxy.pid,
                                            },
                                            result=wave,
                                            progress=lambda: write_report(output_path, report),
                                        )
                                    )
                                    wave["finished_at"] = datetime.now(UTC).isoformat()
                                    write_report(output_path, report)
                                    print(
                                        f"{strategy} clients={clients} {phase} {repeat + 1}: "
                                        f"{sum(row['success'] for row in wave['attempts'])}"
                                        f"/{clients} successful",
                                        flush=True,
                                    )
            report["status"] = (
                "complete_with_failures"
                if any(
                    not attempt["success"] and not attempt["rejected"]
                    for wave in report["waves"]
                    for attempt in wave["attempts"]
                )
                else "complete"
            )
        report["database"]["schema_removed"] = True
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = type(exc).__name__
        raise
    finally:
        if native is not None and native.process is not None:
            report["processes"][0]["exit_code"] = native.process.returncode
        report["finished_at"] = datetime.now(UTC).isoformat()
        write_report(output_path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "benchmarks/configs/phase5-stack.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.config, args.output)
    print(f"{report['status']}: raw measurements saved to {args.output}")
    if report["status"] != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
