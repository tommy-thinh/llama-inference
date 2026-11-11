"""Run the dependency-free JavaScript behavioral suite with ordinary pytest."""

import shutil
import subprocess
from pathlib import Path


def test_frontend_node_suite() -> None:
    node = shutil.which("node")
    assert node is not None, "Install the development Node.js dependency from environment.yml"
    root = Path(__file__).resolve().parents[1]
    tests = sorted((root / "tests" / "frontend").glob("*.test.mjs"))
    assert tests, "The frontend behavioral suite must not be empty"
    result = subprocess.run(
        [node, "--test", "--test-isolation=none", *map(str, tests)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
