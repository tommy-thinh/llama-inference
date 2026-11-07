import hashlib
import io
import struct
from pathlib import Path
from urllib.error import URLError

import pytest

from inference import artifacts, setup

LLAMA_TEMPLATE = (
    "<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\n"
    "hello<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n"
)
LLAMA_SPECIAL_TOKENS = [
    "<|begin_of_text|>",
    "<|start_header_id|>",
    "<|end_header_id|>",
    "<|eot_id|>",
]


def string(value: str) -> bytes:
    encoded = value.encode()
    return struct.pack("<Q", len(encoded)) + encoded


def string_array(values: list[str]) -> bytes:
    return struct.pack("<IQ", 8, len(values)) + b"".join(string(value) for value in values)


def gguf(entries: list[tuple[str, int, bytes]]) -> bytes:
    return (
        b"GGUF"
        + struct.pack("<IQQ", 3, 0, len(entries))
        + b"".join(string(key) + struct.pack("<I", kind) + value for key, kind, value in entries)
    )


def model_bytes(template: str = LLAMA_TEMPLATE) -> bytes:
    return gguf(
        [
            ("general.architecture", 8, string("llama")),
            ("general.file_type", 4, struct.pack("<I", 15)),
            ("llama.block_count", 4, struct.pack("<I", 16)),
            ("llama.embedding_length", 4, struct.pack("<I", 2048)),
            ("llama.attention.head_count", 4, struct.pack("<I", 32)),
            ("llama.attention.head_count_kv", 4, struct.pack("<I", 8)),
            ("tokenizer.ggml.model", 8, string("gpt2")),
            ("tokenizer.ggml.pre", 8, string("llama-bpe")),
            ("tokenizer.ggml.bos_token_id", 4, struct.pack("<I", 128000)),
            ("tokenizer.ggml.eos_token_id", 4, struct.pack("<I", 128009)),
            (
                "tokenizer.ggml.tokens",
                9,
                string_array(
                    [
                        *LLAMA_SPECIAL_TOKENS,
                        *[f"token-{index}" for index in range(61)],
                    ]
                ),
            ),
            ("tokenizer.chat_template", 8, string(template)),
        ]
    )


def verification_lock(content: bytes) -> dict[str, object]:
    return {
        **artifacts.load_lock()["model"],
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def test_read_metadata_handles_utf8_small_arrays_and_large_vocabulary(tmp_path: Path) -> None:
    path = tmp_path / "header.gguf"
    path.write_bytes(
        gguf(
            [
                ("name", 8, string("Llama tiếng Việt")),
                ("layers", 9, struct.pack("<IQIII", 4, 3, 1, 2, 3)),
                ("vocab", 9, struct.pack("<IQ", 8, 65) + b"".join(string("x") for _ in range(65))),
            ]
        )
    )
    metadata = artifacts.read_gguf_metadata(path)["metadata"]
    assert metadata == {
        "name": "Llama tiếng Việt",
        "layers": [1, 2, 3],
        "vocab": {"element_type": 8, "length": 65},
    }


@pytest.mark.parametrize(
    "data",
    [
        b"bad!",
        b"GGUF" + struct.pack("<I", 2),
        b"GGUF" + struct.pack("<IQQ", 3, 0, 1),
        gguf([("invalid", 99, b"")]),
        gguf([("oversized", 8, struct.pack("<Q", 17 * 1024 * 1024))]),
        b"GGUF" + struct.pack("<IQQ", 3, 0, 10001),
        gguf([("oversized-array", 9, struct.pack("<IQ", 4, 2_000_001))]),
        gguf([("nested-array", 9, struct.pack("<IQ", 9, 1))]),
    ],
)
def test_reject_invalid_or_truncated_headers(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "invalid.gguf"
    path.write_bytes(data)
    with pytest.raises(ValueError):
        artifacts.read_gguf_metadata(path)


def test_verify_checks_bytes_and_model_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "model.gguf"
    content = model_bytes()
    path.write_bytes(content)
    expected = verification_lock(content)
    monkeypatch.setattr(artifacts, "load_lock", lambda: {"model": expected})
    assert artifacts.verify_model(path)["sha256"] == expected["sha256"]
    path.write_bytes(path.read_bytes().replace(b"llama", b"other", 1))
    with pytest.raises(ValueError, match="SHA-256"):
        artifacts.verify_model(path)
    expected["sha256"] = artifacts.sha256_file(path)
    with pytest.raises(ValueError, match="architecture"):
        artifacts.verify_model(path)
    path.write_bytes(b"truncated")
    with pytest.raises(ValueError, match="size"):
        artifacts.verify_model(path)


def test_model_lock_pins_community_licensed_llama_conversion() -> None:
    model = artifacts.load_lock()["model"]
    assert model["upstream"] == "meta-llama/Llama-3.2-1B-Instruct"
    assert model["upstream_access"] == "manual-gated"
    assert model["quantized_by"] == "bartowski"
    assert model["quantizer"] == "llama.cpp b3821 with imatrix"
    assert "official upstream" in model["chat_template_source"]
    assert model["repository"] == "bartowski/Llama-3.2-1B-Instruct-GGUF"
    assert model["revision"] == "067b946cf014b7c697f3654f621d577a3e3afd1c"
    assert model["filename"] == "Llama-3.2-1B-Instruct-Q4_K_M.gguf"
    assert model["size_bytes"] == 807_694_464
    assert model["sha256"] == "6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83"
    assert model["quantization"] == "Q4_K_M"
    assert model["architecture"] == "llama"
    assert model["license"] == "llama3.2"
    assert model["license_name"] == "Llama 3.2 Community License"
    assert "apache" not in model["license_name"].lower()
    assert model["gguf_metadata"]["tokenizer.ggml.bos_token_id"] == 128000
    assert model["gguf_metadata"]["tokenizer.ggml.eos_token_id"] == 128009


def test_download_does_not_publish_unverified_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "model.gguf"
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"bad"))
    with pytest.raises(ValueError, match="size"):
        setup.download_model(destination, license_accepted=True)
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_download_refuses_without_license_acceptance_before_network_or_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "models" / "model.gguf"
    requests = []

    def unexpected_request(*args: object, **kwargs: object) -> None:
        requests.append(args)
        pytest.fail("License refusal must happen before any network request")

    monkeypatch.setattr(setup.urllib.request, "urlopen", unexpected_request)
    with pytest.raises(RuntimeError, match="license"):
        setup.download_model(destination, license_accepted=False)
    assert requests == []
    assert not destination.parent.exists()


def test_download_preserves_existing_invalid_model(tmp_path: Path) -> None:
    destination = tmp_path / "model.gguf"
    destination.write_bytes(b"existing")
    with pytest.raises(ValueError, match="size"):
        setup.download_model(destination, license_accepted=True)
    assert destination.read_bytes() == b"existing"


def test_connection_failure_leaves_no_partial_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "model.gguf"

    def unavailable(*args: object, **kwargs: object) -> None:
        raise URLError("connection unavailable")

    monkeypatch.setattr(setup.urllib.request, "urlopen", unavailable)
    with pytest.raises(URLError, match="connection unavailable"):
        setup.download_model(destination, license_accepted=True)
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_read_failure_cleans_up_and_closes_the_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class InterruptedDownload(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            if self.tell():
                raise OSError("connection reset during download")
            return super().read(3)

    response = InterruptedDownload(b"incomplete model bytes")
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *args, **kwargs: response)
    with pytest.raises(OSError, match="connection reset"):
        setup.download_model(tmp_path / "model.gguf", license_accepted=True)
    assert response.closed
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bos", ["<|begin_of_text|>", "{{- bos_token }}"])
def test_verified_download_is_published_from_the_pinned_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bos: str
) -> None:
    destination = tmp_path / "model.gguf"
    content = model_bytes(LLAMA_TEMPLATE.replace("<|begin_of_text|>", bos))
    expected = verification_lock(content)
    monkeypatch.setattr(artifacts, "load_lock", lambda: {"model": expected})
    model = setup.load_lock()["model"]
    urls = []

    def download(url: str, timeout: int) -> io.BytesIO:
        assert not destination.exists()
        assert timeout > 0
        urls.append(url)
        return io.BytesIO(content)

    monkeypatch.setattr(setup.urllib.request, "urlopen", download)
    setup.download_model(destination, license_accepted=True)
    assert urls == [
        f"https://huggingface.co/{model['repository']}/resolve/"
        f"{model['revision']}/{model['filename']}"
    ]
    assert destination.read_bytes() == content
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize(
    "key,kind,original,replacement",
    [
        ("general.file_type", 4, struct.pack("<I", 15), struct.pack("<I", 1)),
        ("llama.block_count", 4, struct.pack("<I", 16), struct.pack("<I", 15)),
        ("llama.embedding_length", 4, struct.pack("<I", 2048), struct.pack("<I", 1536)),
        ("llama.attention.head_count", 4, struct.pack("<I", 32), struct.pack("<I", 16)),
        ("llama.attention.head_count_kv", 4, struct.pack("<I", 8), struct.pack("<I", 4)),
        ("tokenizer.ggml.model", 8, string("gpt2"), string("other")),
        ("tokenizer.ggml.pre", 8, string("llama-bpe"), string("other")),
        ("tokenizer.ggml.bos_token_id", 4, struct.pack("<I", 128000), struct.pack("<I", 0)),
        ("tokenizer.ggml.eos_token_id", 4, struct.pack("<I", 128009), struct.pack("<I", 0)),
        (
            "tokenizer.chat_template",
            8,
            string(LLAMA_TEMPLATE),
            string("user: hello\nassistant:"),
        ),
    ],
)
def test_checksum_identity_does_not_replace_model_compatibility_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    kind: int,
    original: bytes,
    replacement: bytes,
) -> None:
    encoded_key = string(key) + struct.pack("<I", kind)
    content = model_bytes().replace(encoded_key + original, encoded_key + replacement)
    path = tmp_path / "incompatible.gguf"
    path.write_bytes(content)
    expected = verification_lock(content)
    monkeypatch.setattr(artifacts, "load_lock", lambda: {"model": expected})
    with pytest.raises(ValueError):
        artifacts.verify_model(path)


@pytest.mark.parametrize(
    "missing_token",
    ["<|begin_of_text|>", "<|start_header_id|>", "<|end_header_id|>", "<|eot_id|>"],
)
def test_verifier_requires_llama_chat_template_special_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_token: str
) -> None:
    key = string("tokenizer.chat_template") + struct.pack("<I", 8)
    content = model_bytes().replace(
        key + string(LLAMA_TEMPLATE),
        key + string(LLAMA_TEMPLATE.replace(missing_token, "")),
    )
    path = tmp_path / "incompatible-template.gguf"
    path.write_bytes(content)
    expected = verification_lock(content)
    monkeypatch.setattr(artifacts, "load_lock", lambda: {"model": expected})
    with pytest.raises(ValueError, match="template"):
        artifacts.verify_model(path)


@pytest.mark.parametrize("missing_token", LLAMA_SPECIAL_TOKENS)
@pytest.mark.parametrize("bos", ["<|begin_of_text|>", "{{- bos_token }}"])
def test_verifier_requires_llama_special_tokens_in_vocabulary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_token: str, bos: str
) -> None:
    original = string_array([*LLAMA_SPECIAL_TOKENS, *[f"token-{index}" for index in range(61)]])
    changed_tokens = [
        "<|replacement|>" if token == missing_token else token
        for token in [*LLAMA_SPECIAL_TOKENS, *[f"token-{index}" for index in range(61)]]
    ]
    content = model_bytes(LLAMA_TEMPLATE.replace("<|begin_of_text|>", bos)).replace(
        original, string_array(changed_tokens)
    )
    path = tmp_path / "incompatible-vocabulary.gguf"
    path.write_bytes(content)
    expected = verification_lock(content)
    monkeypatch.setattr(artifacts, "load_lock", lambda: {"model": expected})
    with pytest.raises(ValueError, match="special tokens"):
        artifacts.verify_model(path)


@pytest.mark.parametrize("bos", ["{{- bos_token }}", "{{ bos_token }}", "{{- bos_token -}}"])
def test_verifier_accepts_template_emitting_the_model_bos_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bos: str
) -> None:
    # The pinned GGUF uses Jinja output; llama.cpp supplies bos_token from its vocabulary.
    content = model_bytes(LLAMA_TEMPLATE.replace("<|begin_of_text|>", bos))
    path = tmp_path / "jinja-bos.gguf"
    path.write_bytes(content)
    expected = verification_lock(content)
    monkeypatch.setattr(artifacts, "load_lock", lambda: {"model": expected})
    assert artifacts.verify_model(path)["sha256"] == expected["sha256"]


@pytest.mark.parametrize(
    "bos",
    [
        "bos_token",
        "{{ bos_token_extra }}",
        "{# {{ bos_token }} #}",
        "{% set prefix = bos_token %}",
        "{{ 'bos_token' }}",
    ],
)
def test_verifier_rejects_non_emitting_or_wrong_bos_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bos: str
) -> None:
    content = model_bytes(LLAMA_TEMPLATE.replace("<|begin_of_text|>", bos))
    path = tmp_path / "wrong-bos-template.gguf"
    path.write_bytes(content)
    expected = verification_lock(content)
    monkeypatch.setattr(artifacts, "load_lock", lambda: {"model": expected})
    with pytest.raises(ValueError, match="template"):
        artifacts.verify_model(path)


@pytest.mark.parametrize("missing_marker", LLAMA_SPECIAL_TOKENS[1:])
def test_bos_variable_does_not_replace_the_other_llama_template_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_marker: str
) -> None:
    template = LLAMA_TEMPLATE.replace("<|begin_of_text|>", "{{- bos_token }}")
    content = model_bytes(template.replace(missing_marker, ""))
    path = tmp_path / "missing-chat-marker.gguf"
    path.write_bytes(content)
    expected = verification_lock(content)
    monkeypatch.setattr(artifacts, "load_lock", lambda: {"model": expected})
    with pytest.raises(ValueError, match="template"):
        artifacts.verify_model(path)


@pytest.mark.parametrize("token_kind", ["bos", "eos"])
def test_verifier_requires_special_token_id_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token_kind: str
) -> None:
    key = f"tokenizer.ggml.{token_kind}_token_id"
    replacement_key = f"tokenizer.ggml.{token_kind}_unusedid"
    content = model_bytes().replace(string(key), string(replacement_key))
    path = tmp_path / "missing-token-id.gguf"
    path.write_bytes(content)
    expected = verification_lock(content)
    monkeypatch.setattr(artifacts, "load_lock", lambda: {"model": expected})
    with pytest.raises(ValueError, match=key):
        artifacts.verify_model(path)
