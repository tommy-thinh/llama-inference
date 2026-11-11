"""Independent deployment contracts; never installs or exposes services.

Requirements: docs/deployment.md and docs/deployment.md. Caddy checks
use an optional staged binary and random loopback HTTP ports, with ACME disabled.
"""

import configparser
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture
def caddy_binary():
    configured = os.environ.get("LLAMA_TEST_CADDY")
    staged = ROOT / ".runtime/caddy-test/extracted/usr/bin/caddy"
    binary = configured or shutil.which("caddy") or (str(staged) if staged.is_file() else None)
    if not binary:
        pytest.skip(
            "Caddy unavailable; set LLAMA_TEST_CADDY to a staged binary (no install needed)"
        )
    return binary


@contextmanager
def mock_backend():
    seen = []
    cancelled = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            seen.append((self.path, dict(self.headers)))
            if self.path == "/api/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    for _ in range(100):
                        self.wfile.write(b"data: hello\n\n")
                        self.wfile.flush()
                        time.sleep(0.03)
                except (BrokenPipeError, ConnectionResetError):
                    cancelled.set()
                finally:
                    self.close_connection = True
            else:
                body = json.dumps({"path": self.path, "headers": dict(self.headers)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, seen, cancelled
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def proxy(tmp_path, caddy_binary):
    static = tmp_path / "frontend"
    legal = tmp_path / "legal"
    static.mkdir()
    legal.mkdir()
    for name in ("index.html", "app.mjs", "stream.mjs", "style.css"):
        shutil.copyfile(ROOT / "frontend" / name, static / name)
    for name in (".env", "secret.txt", "backup.dump"):
        (static / name).write_text("PRIVATE_SENTINEL")
    for name in ("NOTICE", "LLAMA-3.2-LICENSE.txt", "LLAMA-3.2-USE-POLICY.md"):
        (legal / name).write_text("license:" + name)
    (legal / "secret.txt").write_text("PRIVATE_SENTINEL")
    port = free_port()
    env = {
        **os.environ,
        "LLAMA_DOMAIN": f"http://127.0.0.1:{port}",
        "LLAMA_ACME_EMAIL": "tester@example.invalid",
        "LLAMA_STATIC_ROOT": str(static),
        "LLAMA_LEGAL_ROOT": str(legal),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    with mock_backend() as (upstream_port, seen, cancelled):
        source = (
            (ROOT / "deploy/Caddyfile")
            .read_text()
            .replace("127.0.0.1:8000", f"127.0.0.1:{upstream_port}")
        )
        # The production template is unchanged; the test explicitly opts out of TLS/admin.
        source = source.replace("admin 127.0.0.1:2019", "admin off")
        source = source.replace("{\n", "{\n\tauto_https off\n", 1)
        config = tmp_path / "Caddyfile"
        config.write_text(source)
        validation = subprocess.run(
            [caddy_binary, "validate", "--config", str(config), "--adapter", "caddyfile"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert validation.returncode == 0, validation.stderr
        with (tmp_path / "caddy.log").open("w+") as log:
            process = subprocess.Popen(
                [caddy_binary, "run", "--config", str(config), "--adapter", "caddyfile"],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3) as client:
                    deadline = time.monotonic() + 8
                    while True:
                        try:
                            client.get("/")
                            break
                        except httpx.ConnectError:
                            if process.poll() is not None or time.monotonic() >= deadline:
                                log.seek(0)
                                pytest.fail("Caddy startup failed: " + log.read())
                            time.sleep(0.03)
                    yield client, seen, cancelled
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


def test_proxy_serves_only_frontend_assets_and_approved_backend_paths(proxy):
    client, seen, _ = proxy
    html = (ROOT / "frontend/index.html").read_text()
    assets = re.findall(r'(?:src|href)="(/static/[^\"]+)"', html)
    app_path = next(path for path in assets if path.endswith("app.mjs"))
    imports = re.findall(r"from\s+['\"]([^'\"]+)['\"]", (ROOT / "frontend/app.mjs").read_text())
    assets += [urljoin(app_path, relative) for relative in imports]
    for path in ("/", "/index.html", *assets):
        response = client.get(path)
        assert response.status_code == 200, path
        filename = "index.html" if path == "/" else path.rsplit("/", 1)[1]
        assert response.text == (ROOT / "frontend" / filename).read_text()
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert "server" not in response.headers
    for path in ("/health", "/ready", "/api/conversations"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json()["path"] == path
    for name in ("NOTICE", "LLAMA-3.2-LICENSE.txt", "LLAMA-3.2-USE-POLICY.md"):
        response = client.get("/legal/" + name)
        assert response.status_code == 200
        assert response.text == "license:" + name
    before = len(seen)
    for path in (
        "/api/chat",
        "/api/chat/",
        "/api/%63hat",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/v1/models",
        "/completion",
        "/metrics",
        "/.env",
        "/secret.txt",
        "/backup.dump",
        "/legal/secret.txt",
        "/frontend/index.html",
        "/app.mjs",
        "/stream.mjs",
        "/style.css",
        "/.runtime/database.env",
        "/app.mjs/extra",
    ):
        response = client.get(path)
        assert response.status_code in (403, 404), (path, response.status_code)
        assert "PRIVATE_SENTINEL" not in response.text
    assert len(seen) == before, "A blocked path reached the backend"


def test_proxy_overwrites_spoofed_forwarding_headers(proxy):
    client, _, _ = proxy
    response = client.get(
        "/api/conversations",
        headers={
            "X-Real-IP": "198.51.100.2",
            "X-Forwarded-For": "198.51.100.3",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "attacker.invalid",
            "Forwarded": "for=198.51.100.4;host=attacker.invalid;proto=https",
        },
    )
    headers = {key.lower(): value for key, value in response.json()["headers"].items()}
    assert headers["x-real-ip"] == "127.0.0.1"
    assert headers.get("x-forwarded-for") in (None, "127.0.0.1")
    assert headers.get("x-forwarded-proto") in (None, "http")
    assert "attacker.invalid" not in headers.get("x-forwarded-host", "")
    assert "forwarded" not in headers


def test_proxy_streams_progressively_and_aborts_upstream_on_disconnect(proxy):
    client, _, cancelled = proxy
    started = time.monotonic()
    with client.stream("GET", "/api/stream") as response:
        assert response.status_code == 200
        assert next(response.iter_lines()) == "data: hello"
        assert time.monotonic() - started < 1, "Caddy buffered the SSE response"
    assert cancelled.wait(2), "Browser cancellation did not close the upstream stream"


def test_service_templates_have_restart_resource_and_privilege_boundaries():
    units = list((ROOT / "deploy/systemd").glob("*.service"))
    assert len(units) >= 2, "Prepare native inference and single-worker backend units"
    for unit in units:
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read(unit)
        service = config["Service"]
        assert service.get("User") not in (None, "", "root"), unit
        assert service.get("Restart") in ("on-failure", "always"), unit
        assert service.get("MemoryMax"), unit
        assert service.get("CPUQuota"), unit
        assert service.get("TasksMax"), unit
        assert service.get("NoNewPrivileges", "").lower() in ("true", "yes"), unit
        assert "0.0.0.0" not in service.get("ExecStart", ""), unit
        assert "--workers 2" not in service.get("ExecStart", ""), unit


def test_systemd_verifies_prepared_units_without_installing(tmp_path):
    analyzer = shutil.which("systemd-analyze")
    if not analyzer:
        pytest.skip("systemd-analyze unavailable; prepared service syntax not checked")
    units = []
    for source in (ROOT / "deploy/systemd").glob("*.service"):
        target = tmp_path / source.name
        contents = source.read_text()
        # Verification needs an existing executable, not a copied production runtime.
        contents = contents.replace("/opt/llama/env/bin/python", "/usr/bin/true")
        target.write_text(contents)
        units.append(str(target))
    result = subprocess.run(
        [analyzer, "verify", "--man=no", *units],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def backup_environment(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    dump = tools / "pg_dump"
    dump.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "target = next((a.split('=', 1)[1] for a in args if a.startswith('--file=')), None)\n"
        "if target is None:\n"
        "    target = args[args.index('--file')+1] if '--file' in args else "
        "args[args.index('-f')+1]\n"
        "pathlib.Path(target).write_bytes(b'PGDMP-private-history')\n"
        "sys.exit(42 if os.environ.get('DUMP_FAIL') else 0)\n"
    )
    restore = tools / "pg_restore"
    restore.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        "sys.exit(43 if os.environ.get('RESTORE_FAIL') else 0)\n"
    )
    dump.chmod(0o700)
    restore.chmod(0o700)
    password = tmp_path / "pgpass"
    password.write_text("127.0.0.1:5432:backup_test:tester:PRIVATE_PASSWORD_SENTINEL\n")
    password.chmod(0o600)
    return {
        **os.environ,
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "PGHOST": "127.0.0.1",
        "PGPORT": "5432",
        "PGDATABASE": "backup_test",
        "PGUSER": "tester",
        "PGPASSFILE": str(password),
    }


def run_backup(destination, env):
    return subprocess.run(
        ["bash", str(ROOT / "deploy/backup-database.sh"), str(destination)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize("failure", ["DUMP_FAIL", "RESTORE_FAIL"])
def test_backup_never_publishes_partial_or_invalid_archive(tmp_path, backup_environment, failure):
    destination = tmp_path / "backups"
    destination.mkdir(mode=0o700)
    previous = destination / "previous.dump"
    previous.write_bytes(b"previous-good-archive")
    previous.chmod(0o600)
    result = run_backup(destination, {**backup_environment, failure: "1"})
    assert result.returncode != 0
    assert list(destination.iterdir()) == [previous], "Failure left a partial/new backup"
    assert previous.read_bytes() == b"previous-good-archive"
    assert "PRIVATE_PASSWORD_SENTINEL" not in result.stdout + result.stderr


def test_backup_archive_and_directory_are_private(tmp_path, backup_environment):
    destination = tmp_path / "new-backups"
    result = run_backup(destination, backup_environment)
    assert result.returncode == 0, result.stderr
    files = list(destination.iterdir())
    assert len(files) == 1
    assert files[0].read_bytes() == b"PGDMP-private-history"
    assert files[0].stat().st_mode & 0o777 == 0o600
    assert destination.stat().st_mode & 0o777 == 0o700
    assert "PRIVATE_PASSWORD_SENTINEL" not in result.stdout + result.stderr


@pytest.mark.parametrize("unsafe", ["public-directory", "public-password", "remote-host"])
def test_backup_rejects_unsafe_storage_or_credentials(tmp_path, backup_environment, unsafe):
    destination = tmp_path / "backups"
    destination.mkdir(mode=0o700)
    if unsafe == "public-directory":
        destination.chmod(0o755)
    elif unsafe == "public-password":
        Path(backup_environment["PGPASSFILE"]).chmod(0o644)
    else:
        backup_environment["PGHOST"] = "198.51.100.2"
    result = run_backup(destination, backup_environment)
    assert result.returncode != 0
    assert list(destination.iterdir()) == []
    assert "PRIVATE_PASSWORD_SENTINEL" not in result.stdout + result.stderr


@pytest.mark.database
def test_backup_restores_real_postgresql_data_in_an_isolated_test_schema(tmp_path):
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool

    raw = os.environ.get("LLAMA_TEST_DATABASE_URL")
    if not raw:
        pytest.fail("--run-database requires LLAMA_TEST_DATABASE_URL")
    url = make_url(raw)
    if url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_test"):
        pytest.fail("Refusing restore test: require a dedicated PostgreSQL *_test database")
    if url.host not in ("127.0.0.1", "::1"):
        pytest.fail("Restore test requires the loopback-only test database")
    dump = shutil.which("pg_dump")
    restore = shutil.which("pg_restore")
    assert dump and restore, "PostgreSQL backup/restore executables are required"
    schema = "test_backup_" + uuid.uuid4().hex
    password = tmp_path / "pgpass"

    def escape(value):
        return str(value).replace("\\", "\\\\").replace(":", "\\:")

    password.write_text(
        ":".join(
            escape(value)
            for value in (
                url.host,
                url.port or 5432,
                url.database,
                url.username,
                url.password or "",
            )
        )
        + "\n"
    )
    password.chmod(0o600)
    tools = tmp_path / "tools"
    tools.mkdir()
    wrapper = tools / "pg_dump"
    # Restrict the real dump to this owned schema, including during parallel DB tests.
    wrapper.write_text(f'#!/bin/sh\nexec "{dump}" --schema="$BACKUP_TEST_SCHEMA" "$@"\n')
    wrapper.chmod(0o700)
    env = {
        **os.environ,
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "PGHOST": url.host,
        "PGPORT": str(url.port or 5432),
        "PGDATABASE": url.database,
        "PGUSER": url.username,
        "PGPASSFILE": str(password),
        "BACKUP_TEST_SCHEMA": schema,
    }
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'CREATE TABLE "{schema}".sample (id integer, value text)'))
            connection.execute(
                text(f'INSERT INTO "{schema}".sample VALUES (1, :value)'),
                {"value": "Restored history: học máy"},
            )
        destination = tmp_path / "real-backups"
        result = run_backup(destination, env)
        assert result.returncode == 0, "Real pg_dump/archive validation failed"
        archives = list(destination.glob("*.dump"))
        assert len(archives) == 1
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        restored = subprocess.run(
            [
                restore,
                "--no-password",
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
                "--dbname",
                url.database,
                str(archives[0]),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert restored.returncode == 0, "Real pg_restore failed in isolated test database"
        with engine.connect() as connection:
            row = connection.execute(text(f'SELECT id, value FROM "{schema}".sample')).one()
            assert tuple(row) == (1, "Restored history: học máy")
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
