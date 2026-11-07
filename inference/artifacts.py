"""Validate pinned assets and inspect GGUF metadata without loading tensor weights."""

import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Any, BinaryIO

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "inference/artifacts.lock.json"
_FORMATS = {
    0: "B",
    1: "b",
    2: "H",
    3: "h",
    4: "I",
    5: "i",
    6: "f",
    7: "?",
    10: "Q",
    11: "q",
    12: "d",
}


def load_lock() -> dict[str, Any]:
    return json.loads(LOCK_PATH.read_text())


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _read(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("Truncated GGUF metadata")
    return data


def _number(stream: BinaryIO, fmt: str) -> Any:
    return struct.unpack("<" + fmt, _read(stream, struct.calcsize("<" + fmt)))[0]


def _string(stream: BinaryIO) -> str:
    size = _number(stream, "Q")
    if size > 16 * 1024 * 1024:
        raise ValueError("Unexpectedly large GGUF metadata string")
    return _read(stream, size).decode("utf-8")


def _value(stream: BinaryIO, kind: int, *, capture_token_markers: bool = False) -> Any:
    if kind in _FORMATS:
        return _number(stream, _FORMATS[kind])
    if kind == 8:
        return _string(stream)
    if kind == 9:
        element_type = _number(stream, "I")
        count = _number(stream, "Q")
        if count > 2_000_000 or element_type == 9:
            raise ValueError("Unsupported GGUF metadata array")
        # Keep small architecture arrays; avoid retaining the entire vocabulary.
        values = []
        for _ in range(count):
            item = _value(stream, element_type)
            if count <= 64:
                values.append(item)
            elif capture_token_markers and item in {
                "<|begin_of_text|>",
                "<|start_header_id|>",
                "<|end_header_id|>",
                "<|eot_id|>",
            }:
                values.append(item)
        if count <= 64:
            return values
        result = {"element_type": element_type, "length": count}
        if capture_token_markers:
            result["special_tokens"] = values
        return result
    raise ValueError(f"Unsupported GGUF metadata type: {kind}")


def read_gguf_metadata(path: Path) -> dict[str, Any]:
    """Read little-endian GGUF v3 headers only; this is not an inference implementation."""
    with path.open("rb") as stream:
        if _read(stream, 4) != b"GGUF" or _number(stream, "I") != 3:
            raise ValueError("Expected a little-endian GGUF v3 file")
        tensor_count = _number(stream, "Q")
        metadata_count = _number(stream, "Q")
        if metadata_count > 10000:
            raise ValueError("Unexpected GGUF metadata count")
        metadata = {}
        for _ in range(metadata_count):
            key = _string(stream)
            metadata[key] = _value(
                stream,
                _number(stream, "I"),
                capture_token_markers=key == "tokenizer.ggml.tokens",
            )
    return {"gguf_version": 3, "tensor_count": tensor_count, "metadata": metadata}


def verify_model(path: Path) -> dict[str, Any]:
    model = load_lock()["model"]
    expected = model
    if path.stat().st_size != expected["size_bytes"]:
        raise ValueError("Model size differs from inference/artifacts.lock.json")
    digest = sha256_file(path)
    if digest != expected["sha256"]:
        raise ValueError("Model SHA-256 differs from inference/artifacts.lock.json")
    header = read_gguf_metadata(path)
    metadata = header["metadata"]
    checks = {"general.architecture": model["architecture"], **model["gguf_metadata"]}
    for key, expected_value in checks.items():
        if metadata.get(key) != expected_value:
            raise ValueError(f"Unexpected {key}: {metadata.get(key)!r}")
    template = metadata.get("tokenizer.chat_template", "")
    for marker in model["chat_template_markers"]:
        # The pinned template emits BOS through llama.cpp's Jinja variable.
        if marker == "<|begin_of_text|>" and re.match(r"\s*\{\{-?\s*bos_token\s*-?\}\}", template):
            continue
        if marker not in template:
            raise ValueError(f"Expected Llama chat-template marker {marker}")
    token_values = metadata.get("tokenizer.ggml.tokens", {})
    present_tokens = token_values.get("special_tokens", [])
    missing_tokens = set(model["chat_template_markers"]) - set(present_tokens)
    if missing_tokens:
        raise ValueError(f"Missing special tokens from GGUF vocabulary: {sorted(missing_tokens)}")
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": digest, **header}
