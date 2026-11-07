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
    ]
