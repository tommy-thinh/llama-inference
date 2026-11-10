"""Opt-in real public profile over certificate-verified loopback HTTPS.

This is not deployment: every listener is loopback, the certificate is temporary,
and PostgreSQL changes stay in the existing isolated *_test schema fixture.
"""

import json
import os
import shutil
import ssl
import subprocess
import sys
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import text
from test_deployment import ROOT, free_port
from test_deployment import caddy_binary as caddy_binary
from test_persistence import database_schema as database_schema

pytestmark = [pytest.mark.database, pytest.mark.inference]


@contextmanager
def managed_process(command, env, log_path):
    with log_path.open("w") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            yield process
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def wait_ready(client, process, path="/ready"):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        assert process.poll() is None, "Owned test process exited before readiness"
        try:
            response = client.get(path)
            if response.status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.05)
    pytest.fail("Owned loopback test process did not become ready within 15 seconds")


def generate(client, path):
    response = client.post(
        path + "/chat",
        json={
            "content": "What is 2 + 2? Answer only the number.",
            "max_tokens": 16,
            "temperature": 0,
        },
    )
    assert response.status_code == 200, response.text
    assert "event: error" not in response.text
    assert response.text.rstrip().endswith("data: [DONE]")
    chunks = []
    for line in response.text.splitlines():
        if line.startswith("data:") and line[5:].strip() != "[DONE]":
            event = json.loads(line[5:])
            chunks += [
                choice.get("delta", {}).get("content") or "" for choice in event.get("choices", [])
            ]
    assert "4" in "".join(chunks)


def test_real_public_https_generation_restart_and_cancel(
    tmp_path, database_schema, live_server, caddy_binary
):
    openssl = shutil.which("openssl")
    assert openssl, "OpenSSL is needed to create the temporary loopback test certificate"
    certificate = tmp_path / "loopback.crt"
    key = tmp_path / "loopback.key"
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
    backend_port, proxy_port = free_port(), free_port()
    origin = f"https://127.0.0.1:{proxy_port}"
    env = {
        **os.environ,
        "LLAMA_PUBLIC_ORIGIN": origin,
        "LLAMA_SESSION_COOKIE_SECURE": "true",
        "LLAMA_DATABASE_URL": database_schema[0].render_as_string(hide_password=False),
        "LLAMA_API_UPSTREAM_URL": live_server.config.base_url,
        "LLAMA_STATIC_ROOT": str(ROOT / "frontend"),
        "LLAMA_LEGAL_ROOT": str(ROOT / "legal"),
        "LLAMA_ACME_EMAIL": "loopback-test@example.invalid",
        "XDG_DATA_HOME": str(tmp_path / "caddy-data"),
        "XDG_CONFIG_HOME": str(tmp_path / "caddy-config"),
    }
    source = (ROOT / "deploy/Caddyfile").read_text()
    assert source.startswith("{\n") and source.count("{$LLAMA_DOMAIN} {") == 1
    source = source.replace("{\n", "{\n\tauto_https off\n", 1)
    source = source.replace("admin 127.0.0.1:2019", "admin off")
    source = source.replace(
        "{$LLAMA_DOMAIN} {",
        f"{origin} {{\n\tbind 127.0.0.1\n\ttls {certificate} {key}",
    ).replace("127.0.0.1:8000", f"127.0.0.1:{backend_port}")
    config = tmp_path / "Caddyfile.https"
    config.write_text(source)
    validation = subprocess.run(
        [caddy_binary, "validate", "--config", str(config), "--adapter", "caddyfile"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert validation.returncode == 0, validation.stderr
    backend_command = [sys.executable, "-m", "backend", "--port", str(backend_port)]
    tls = ssl.create_default_context(cafile=str(certificate))
    with (
        managed_process(
            [caddy_binary, "run", "--config", str(config), "--adapter", "caddyfile"],
            env,
            tmp_path / "caddy.log",
        ) as caddy,
        httpx.Client(
            base_url=origin,
            verify=tls,
            trust_env=False,
            timeout=45,
            headers={"Origin": origin},
        ) as client,
    ):
        with managed_process(backend_command, env, tmp_path / "backend-first.log") as backend:
            wait_ready(client, backend)
            assert caddy.poll() is None
            page = client.get("/")
            assert page.status_code == 200 and "Built with Llama" in page.text
            session = client.post("/api/session")
            assert session.status_code == 200
            cookie = session.headers["set-cookie"].lower()
            assert all(
                value in cookie for value in ("secure", "httponly", "samesite=lax", "path=/api")
            )
            conversation = client.post("/api/conversations", json={"title": "HTTPS smoke"})
            assert conversation.status_code in (200, 201)
            path = "/api/conversations/" + conversation.json()["id"]
            generate(client, path)
            saved = client.get(path).json()
            assert saved["messages"][-1]["status"] == "completed"
            metrics = client.get(path + "/requests").json()["items"][0]
            assert metrics["status"] == "completed"
            assert metrics["prompt_tokens"] > 0 and metrics["completion_tokens"] > 0
            assert metrics["ttft_seconds"] >= 0
            with database_schema[1].connect() as connection:
                quotas = connection.execute(
                    text("SELECT scope, requests, tokens FROM daily_quotas")
                )
                assert sorted(tuple(row) for row in quotas) == [("ip", 1, 16), ("session", 1, 16)]
            assert (
                client.post(
                    "/api/conversations",
                    json={"title": "Forbidden"},
                    headers={"Origin": "https://attacker.invalid"},
                ).status_code
                == 403
            )
            assert client.post("/api/chat", json={}).status_code == 404
        # The same HTTPS cookie jar must restore durable history after a real restart.
        with managed_process(backend_command, env, tmp_path / "backend-restarted.log") as backend:
            wait_ready(client, backend)
            restored = client.get(path)
            assert restored.status_code == 200
            assert restored.json()["messages"] == saved["messages"]
            chunks = 0
            with client.stream(
                "POST",
                path + "/chat",
                json={
                    "content": "Count from 1 to 100, separated by commas.",
                    "max_tokens": 256,
                    "temperature": 0,
                },
            ) as response:
                assert response.status_code == 200
                for line in response.iter_lines():
                    if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                        continue
                    event = json.loads(line[5:])
                    if any(
                        choice.get("delta", {}).get("content")
                        for choice in event.get("choices", [])
                    ):
                        chunks += 1
                    if chunks == 3:
                        break
            assert chunks == 3
            deadline = time.monotonic() + 8
            while True:
                cancelled = client.get(path).json()
                if cancelled["active_request"] is None:
                    break
                assert time.monotonic() < deadline, "HTTPS cancellation did not finalize"
                time.sleep(0.05)
            assert cancelled["messages"][-1]["status"] == "cancelled"
            assert cancelled["messages"][-1]["content"]
            generate(client, path)
            assert client.get(path).json()["messages"][-1]["status"] == "completed"
            with database_schema[1].connect() as connection:
                quotas = connection.execute(
                    text("SELECT scope, requests, tokens FROM daily_quotas")
                )
                assert sorted(tuple(row) for row in quotas) == [("ip", 3, 288), ("session", 3, 288)]
    assert urlsplit(origin).hostname == "127.0.0.1"
