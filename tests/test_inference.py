import json
import re
from pathlib import Path

import httpx
import pytest

from benchmarks.scripts.metrics import stream_chat
from benchmarks.scripts.run import request_payload, run_benchmark
from inference.artifacts import ROOT, load_lock, verify_model
from inference.server import ManagedServer

pytestmark = pytest.mark.inference


def test_real_artifact_matches_lock(live_server: ManagedServer) -> None:
    report = verify_model(live_server.config.model)
    assert report["metadata"]["general.architecture"] == "llama"
    assert report["metadata"]["tokenizer.ggml.pre"] == "llama-bpe"


def test_startup_health_and_connectivity(live_server: ManagedServer) -> None:
    with httpx.Client(base_url=live_server.config.base_url, trust_env=False) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        models = client.get("/v1/models").json()
        assert any(item["id"] == "llama-3.2-1b-instruct" for item in models["data"])


def test_basic_generation_returns_correct_arithmetic(live_server: ManagedServer) -> None:
    payload = request_payload("What is 2 + 2? Answer with only the number.", 16)
    payload["stream"] = False
    with httpx.Client(timeout=120, trust_env=False) as client:
        response = client.post(live_server.config.base_url + "/v1/chat/completions", json=payload)
    response.raise_for_status()
    body = response.json()
    assert re.fullmatch(r"\s*4[.!]?\s*", body["choices"][0]["message"]["content"])
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["prompt_tokens"] > 0
    assert 0 < body["usage"]["completion_tokens"] <= 16


def test_real_stream_has_progressive_text_usage_and_terminator(live_server: ManagedServer) -> None:
    payload = request_payload("Count from 1 to 10, separated by commas, without explanation.", 48)
    with httpx.Client(timeout=120, trust_env=False) as client:
        result = stream_chat(client, live_server.config.base_url, payload)
    assert result.content_chunks > 1
    assert "1" in result.output and "10" in result.output
    assert 0 < result.ttft_seconds <= result.total_seconds
    assert result.timings["predicted_per_second"] > 0
    assert result.timings["cache_n"] == 0


def test_invalid_request_is_rejected_and_server_stays_healthy(live_server: ManagedServer) -> None:
    with httpx.Client(timeout=10, trust_env=False) as client:
        endpoint = live_server.config.base_url + "/v1/chat/completions"
        for payload in ({"messages": "not-a-list"}, {}, {"messages": [{"role": "user"}]}):
            response = client.post(endpoint, json=payload)
            assert response.status_code == 400
            assert "error" in response.json()
            assert client.get(live_server.config.base_url + "/health").status_code == 200
        response = client.post(
            endpoint, content=b'{"messages":', headers={"content-type": "application/json"}
        )
        assert response.status_code == 400
        assert "error" in response.json()
        assert client.get(live_server.config.base_url + "/health").status_code == 200
        recovery = request_payload("What is 2 + 2? Answer with only the number.", 16)
        recovery["stream"] = False
        response = client.post(endpoint, json=recovery)
        response.raise_for_status()
        assert re.fullmatch(r"\s*4[.!]?\s*", response.json()["choices"][0]["message"]["content"])


def test_tokenizer_and_embedded_chat_template(live_server: ManagedServer) -> None:
    with httpx.Client(timeout=10, trust_env=False) as client:
        text = "Hello, Tommy! Chào 👋"
        response = client.post(
            live_server.config.base_url + "/tokenize", json={"content": text, "add_special": False}
        )
        response.raise_for_status()
        tokens = response.json()["tokens"]
        assert tokens and all(type(token) is int and token >= 0 for token in tokens)
        response = client.post(live_server.config.base_url + "/detokenize", json={"tokens": tokens})
        response.raise_for_status()
        assert response.json()["content"] == text
        response = client.post(
            live_server.config.base_url + "/apply-template",
            json={
                "messages": [{"role": "user", "content": "Hello, Tommy!"}],
            },
        )
        response.raise_for_status()
        prompt = response.json()["prompt"]
        assert "<|start_header_id|>user<|end_header_id|>" in prompt
        assert "Hello, Tommy!" in prompt
        assert "<|eot_id|>" in prompt
        assert "<|start_header_id|>assistant<|end_header_id|>" in prompt
        assert "<|im_start|>" not in prompt
        # This runtime strips the rendered BOS because tokenization adds it separately.
        prompt_tokens = {}
        for add_special in (False, True):
            response = client.post(
                live_server.config.base_url + "/tokenize",
                json={"content": prompt, "add_special": add_special, "parse_special": True},
            )
            response.raise_for_status()
            prompt_tokens[add_special] = response.json()["tokens"]
        bos_id = load_lock()["model"]["gguf_metadata"]["tokenizer.ggml.bos_token_id"]
        assert prompt_tokens[True] == [bos_id, *prompt_tokens[False]]
        assert prompt_tokens[True].count(bos_id) == 1


def test_real_benchmark_smoke_writes_complete_reproducible_result(tmp_path: Path) -> None:
    output = tmp_path / "smoke.json"
    report = run_benchmark(ROOT / "benchmarks/configs/smoke.json", output)
    assert report["status"] == "complete"
    assert len(report["requests"]) == 2  # one warmup, one measured request
    assert report["summary"][0]["requests"] == 1
    assert report["runtime_commit"] == report["artifacts"]["runtime"]["commit"]
    assert report["verified_model_sha256"] == report["artifacts"]["model"]["sha256"]
    assert report["artifacts"]["model"]["filename"] == "Llama-3.2-1B-Instruct-Q4_K_M.gguf"
    assert report["artifacts"]["model"]["quantization"] == "Q4_K_M"
    assert report["gguf"]["size_bytes"] == 807_694_464
    assert report["profiles"][0]["server_exit_code"] == 0
    assert json.loads(output.read_text())["status"] == "complete"
