from pathlib import Path


def test_conda_environment_manifest_contract() -> None:
    manifest = Path(__file__).resolve().parents[1] / "environment.yml"

    assert manifest.read_text().splitlines() == [
        "name: llama",
        "channels:",
        "  - conda-forge",
        "  - nodefaults",
        "dependencies:",
        "  - python=3.11",
        "  - httpx=0.28.1",
        "  - pytest=9.1.1",
        "  - ruff=0.17.1",
        "  - fastapi-core=0.162.2",
        "  - uvicorn=0.54.0",
        "  - pydantic=2.14.3",
        "  - anyio=4.16.0",
        "  - nodejs=24.22.1",
    ]
