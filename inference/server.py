"""Start in the foreground, or own a temporary inference process with bounded cleanup."""

import argparse
import os
import shlex
import socket
import subprocess
import time
from pathlib import Path
from typing import IO

import httpx

from inference.artifacts import ROOT, verify_model
from inference.config import ServerConfig, native_runtime_environment
from inference.setup import check_runtime, checked_runtime_version


def free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def memory_snapshot(pid: int) -> dict[str, int]:
    """Linux process RSS and lifetime high-water mark, in bytes (not whole-machine RAM)."""
    try:
        lines = Path(f"/proc/{pid}/status").read_text().splitlines()
    except (FileNotFoundError, ProcessLookupError):
        return {}
    names = {"VmRSS": "rss_bytes", "VmHWM": "peak_rss_bytes"}
    return {
        names[key]: int(value.split()[0]) * 1024
        for line in lines
        if ":" in line
        for key, value in [line.split(":", 1)]
        if key in names
    }


def process_cpu_seconds(pid: int) -> float | None:
    """Return process user+system CPU seconds from Linux /proc stat counters."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    # comm is parenthesized and may contain spaces; fields after its last ')' start at state (3).
    fields = stat[stat.rfind(")") + 2 :].split()
    if len(fields) < 13:
        return None
    ticks = int(fields[11]) + int(fields[12])  # utime (14) + stime (15)
    return ticks / os.sysconf("SC_CLK_TCK")


class ManagedServer:
    """Own exactly one subprocess; never stop an unrelated process on the same port."""

    def __init__(self, config: ServerConfig, log_path: Path, startup_timeout: float = 120) -> None:
        if startup_timeout <= 0:
            raise ValueError("startup_timeout must be positive")
        self.config = config
        self.log_path = log_path
        self.startup_timeout = startup_timeout
        self.process: subprocess.Popen[bytes] | None = None
        self._log: IO[bytes] | None = None
        self.startup_seconds = 0.0

    @property
    def pid(self) -> int:
        if self.process is None:
            raise RuntimeError("Server has not started")
        return self.process.pid

    def __enter__(self) -> "ManagedServer":
        self.config.validate_files()
        # Fail before launch if another process already owns the requested address.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", self.config.port))
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = self.log_path.open("wb")
        started = time.perf_counter()
        try:
            self.process = subprocess.Popen(
                self.config.command(),
                stdout=self._log,
                stderr=subprocess.STDOUT,
                env=server_environment(self.config.binary),
            )
            with httpx.Client(timeout=1, trust_env=False) as client:
                while time.perf_counter() - started < self.startup_timeout:
                    if self.process.poll() is not None:
                        raise RuntimeError(f"llama-server exited; see {self.log_path}")
                    try:
                        response = client.get(self.config.base_url + "/health")
                        if response.status_code == 200 and response.json().get("status") == "ok":
                            if self.process.poll() is None:
                                self.startup_seconds = time.perf_counter() - started
                                return self
                    except (httpx.HTTPError, ValueError):
                        pass
                    time.sleep(0.1)
            raise TimeoutError(f"llama-server readiness timed out; see {self.log_path}")
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self._log is not None:
            self._log.close()

    def __exit__(self, *_: object) -> None:
        self.stop()


def server_environment(binary: Path | None = None) -> dict[str, str]:
    # Avoid inherited llama.cpp flags silently changing a recorded experiment.
    selected_binary = binary or ServerConfig.local().binary
    environment = native_runtime_environment(selected_binary)
    return {
        key: value
        for key, value in environment.items()
        if not key.startswith(("LLAMA_ARG_", "LLAMA_API_KEY"))
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threads", type=int)
    parser.add_argument("--ctx-size", type=int)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--port", type=int)
    parser.add_argument("--print-command", action="store_true")
    args = parser.parse_args()
    overrides = {
        name: getattr(args, name)
        for name in ("threads", "ctx_size", "max_tokens", "port")
        if getattr(args, name) is not None
    }
    config = ServerConfig.local(**overrides)
    command = config.command()
    print(shlex.join(command), flush=True)
    if not args.print_command:
        config.validate_files()
        check_runtime(ROOT / ".runtime/llama.cpp")
        checked_runtime_version(config.binary)
        verify_model(config.model)
        # Replacing Python means Ctrl+C/systemd signals reach the actual server.
        os.execve(str(config.binary), command, server_environment(config.binary))


if __name__ == "__main__":
    main()
