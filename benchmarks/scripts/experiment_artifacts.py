"""Verify/download pinned experiment artifacts without changing active defaults."""

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx

from inference.artifacts import ROOT, read_gguf_metadata, sha256_file

MANIFEST_PATH = ROOT / "benchmarks/artifacts.lock.json"


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text())


def model_path(model_id: str) -> Path:
    return ROOT / ".runtime/models" / load_manifest()["models"][model_id]["filename"]


def verify_experiment_model(path: Path, model_id: str) -> dict[str, Any]:
    """Check complete bytes and the expected architecture/tokenizer/template."""
    expected = load_manifest()["models"][model_id]
    size = path.stat().st_size
    if size != expected["size_bytes"]:
        raise ValueError("Model size differs from benchmarks/artifacts.lock.json")
    digest = sha256_file(path)
    if digest != expected["sha256"]:
        raise ValueError("Model SHA-256 differs from benchmarks/artifacts.lock.json")
    header = read_gguf_metadata(path)
    metadata = header["metadata"]
    if header["tensor_count"] != expected["tensor_count"]:
        raise ValueError("Unexpected GGUF tensor count")
    checks = {"general.architecture": expected["architecture"], **expected["gguf_metadata"]}
    for key, value in checks.items():
        if metadata.get(key) != value:
            raise ValueError(f"Unexpected {key}: {metadata.get(key)!r}")
    template_hash = hashlib.sha256(metadata.get("tokenizer.chat_template", "").encode()).hexdigest()
    if template_hash != expected["chat_template_sha256"]:
        raise ValueError("Unexpected GGUF chat template SHA-256")
    tokens = metadata.get("tokenizer.ggml.tokens", {})
    if tokens.get("length") != 128256:
        raise ValueError("Unexpected GGUF vocabulary length")
    missing = set(expected["chat_template_markers"]) - set(tokens.get("special_tokens", []))
    if missing:
        raise ValueError(f"Missing GGUF special tokens: {sorted(missing)}")
    return {"path": str(path), "size_bytes": size, "sha256": digest, **header}


def download_model(model_id: str) -> dict[str, Any]:
    """Download to a temporary sibling, verify, then publish without replacing files."""
    destination = model_path(model_id)
    if destination.exists():
        return verify_experiment_model(destination, model_id)
    model = load_manifest()["models"][model_id]
    url = (
        f"https://huggingface.co/{model['repository']}/resolve/"
        f"{model['revision']}/{model['filename']}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".partial", delete=False
        ) as stream:
            temporary = Path(stream.name)
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
                response.raise_for_status()
                size = 0
                for chunk in response.iter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > model["size_bytes"]:
                        raise ValueError("Download exceeds pinned model size")
                    stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        verification = verify_experiment_model(temporary, model_id)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            return verify_experiment_model(destination, model_id)
        return {**verification, "path": str(destination)}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", choices=("q4", "q8"))
    parser.add_argument("--download", action="store_true", help="download only if absent")
    args = parser.parse_args()
    result = (
        download_model(args.model)
        if args.download
        else verify_experiment_model(model_path(args.model), args.model)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
