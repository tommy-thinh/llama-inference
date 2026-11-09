from collections.abc import Iterator
from pathlib import Path

import pytest

from inference.artifacts import ROOT
from inference.config import ServerConfig
from inference.server import ManagedServer, free_local_port


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-inference", action="store_true", help="Start the real CPU inference model"
    )
    parser.addoption(
        "--run-database", action="store_true", help="Use the isolated PostgreSQL test database"
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "database: opt-in isolated PostgreSQL integration tests")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not config.getoption("--run-database"):
        skip_database = pytest.mark.skip(
            reason="Requires --run-database and LLAMA_TEST_DATABASE_URL"
        )
        for item in items:
            if "database" in item.keywords:
                item.add_marker(skip_database)
    if not config.getoption("--run-inference"):
        skip = pytest.mark.skip(reason="Requires --run-inference and verified local runtime/model")
        for item in items:
            if "inference" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def live_server() -> Iterator[ManagedServer]:
    config = ServerConfig.local(threads=4, ctx_size=2048, max_tokens=64, port=free_local_port())
    with ManagedServer(config, ROOT / ".runtime/logs/pytest-inference.log") as server:
        yield server
    assert server.process is not None and server.process.poll() is not None


@pytest.fixture
def fixture_model(tmp_path: Path) -> Path:
    """An empty placeholder only for process/configuration tests; never an actual model."""
    path = tmp_path / "placeholder.gguf"
    path.touch()
    return path
