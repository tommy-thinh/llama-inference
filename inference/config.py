"""Explicit, intentionally conservative settings for one local CPU inference slot."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from inference.artifacts import ROOT, load_lock


def native_runtime_environment(
    binary: Path, inherited: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Make sibling llama.cpp shared libraries visible without changing the host."""
    environment = dict(os.environ if inherited is None else inherited)
    library_directory = str(binary.expanduser().resolve().parent)
    existing = environment.get("LD_LIBRARY_PATH", "").split(os.pathsep)
    environment["LD_LIBRARY_PATH"] = os.pathsep.join(
        [library_directory, *(path for path in existing if path and path != library_directory)]
    )
    return environment


@dataclass(frozen=True)
class ServerConfig:
    binary: Path
    model: Path
    threads: int = 4
    ctx_size: int = 2048
    max_tokens: int = 128
    port: int = 8081

    def __post_init__(self) -> None:
        if not 1 <= self.threads <= (os.cpu_count() or 1):
            raise ValueError("threads must fit the available logical CPUs")
        if not 512 <= self.ctx_size <= 8192:
            raise ValueError("Phase 1 context must be between 512 and 8192 tokens")
        if not 1 <= self.max_tokens <= 512 or self.max_tokens >= self.ctx_size:
            raise ValueError("max_tokens must be between 1 and 512 and below ctx_size")
        if not 1024 <= self.port <= 65535:
            raise ValueError("port must be between 1024 and 65535")

    @classmethod
    def local(cls, **overrides: int) -> "ServerConfig":
        model_name = load_lock()["model"]["filename"]
        values = {
            "threads": os.environ.get("LLAMA_THREADS", "4"),
            "ctx_size": os.environ.get("LLAMA_CTX_SIZE", "2048"),
            "max_tokens": os.environ.get("LLAMA_MAX_TOKENS", "128"),
            "port": os.environ.get("LLAMA_PORT", "8081"),
        }
        values.update(overrides)
        return cls(
            binary=Path(
                os.environ.get(
                    "LLAMA_SERVER_PATH", ROOT / ".runtime/llama.cpp/build/bin/llama-server"
                )
            )
            .expanduser()
            .resolve(),
            model=Path(os.environ.get("LLAMA_MODEL_PATH", ROOT / ".runtime/models" / model_name))
            .expanduser()
            .resolve(),
            **{key: int(value) for key, value in values.items()},
        )

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def command(self) -> list[str]:
        return [
            str(self.binary),
            "--model",
            str(self.model),
            "--alias",
            "llama-3.2-1b-instruct",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--n-gpu-layers",
            "0",
            "--threads",
            str(self.threads),
            "--threads-batch",
            str(self.threads),
            "--threads-http",
            "2",
            "--ctx-size",
            str(self.ctx_size),
            "--parallel",
            "1",
            "--batch-size",
            "256",
            "--ubatch-size",
            "128",
            "--n-predict",
            str(self.max_tokens),
            "--cache-type-k",
            "f16",
            "--cache-type-v",
            "f16",
            "--cache-ram",
            "0",
            "--no-cache-idle-slots",
            "--ctx-checkpoints",
            "0",
            "--flash-attn",
            "on",
            "--fit",
            "off",
            "--jinja",
            "--reasoning",
            "off",
            "--no-context-shift",
            "--no-webui",
            "--offline",
            "--perf",
        ]

    def validate_files(self) -> None:
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise FileNotFoundError(f"Missing executable {self.binary}; run inference.setup")
        if not self.model.is_file():
            raise FileNotFoundError(f"Missing model {self.model}; run inference.setup")
