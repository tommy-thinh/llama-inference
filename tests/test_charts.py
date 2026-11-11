"""Independent chart checks against saved measurements, without running inference."""

import hashlib
import json
import math
import shutil
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from benchmarks.scripts.charts import allocation_chart, comparison, regenerate

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks" / "results"
PHASE5 = RESULTS / "2026-11-10-phase5"
SVG = "{http://www.w3.org/2000/svg}"


def accessible(svg: str) -> str:
    root = ET.fromstring(svg)
    assert root.attrib.get("role") == "img"
    assert root.find(f"{SVG}title") is not None
    assert root.find(f"{SVG}desc") is not None
    return " ".join(root.itertext())


def metadata(svg: str) -> dict:
    root = ET.fromstring(svg)
    node = root.find(f"{SVG}metadata[@id='benchmark-data']")
    assert node is not None and node.text
    return json.loads(node.text)


def test_comparison_preserves_configuration_series_and_full_precision() -> None:
    rows = [
        {
            "profile": "first",
            "prompt_id": "short",
            "clients": 1,
            "median_ttft_seconds": 0.123456789,
        },
        {"profile": "first", "prompt_id": "long", "clients": 1, "median_ttft_seconds": 2.345678901},
        {"profile": "second", "prompt_id": "short", "clients": 2, "median_ttft_seconds": 0.0},
    ]
    svg = comparison(rows, "median_ttft_seconds", "Median TTFT")
    text = accessible(svg).lower()
    for value in ("first", "second", "short", "long", "0.123456789", "2.345678901", "0.0"):
        assert value in text
    data = metadata(svg)
    assert data["metric"] == "median_ttft_seconds"
    assert data["unit"] == "s"
    assert data["scale"] == "linear"  # Zero cannot be placed on a log axis.
    assert data["records"] == [
        {
            "profile": r["profile"],
            "prompt_id": r["prompt_id"],
            "clients": r["clients"],
            "value": r["median_ttft_seconds"],
        }
        for r in rows
    ]
    assert svg == comparison(rows, "median_ttft_seconds", "Median TTFT")


@pytest.mark.parametrize("value", [-1, math.inf, -math.inf, math.nan])
def test_comparison_rejects_invalid_measurements(value: float) -> None:
    rows = [{"profile": "test", "prompt_id": "short", "clients": 1, "median_ttft_seconds": value}]
    with pytest.raises(ValueError):
        comparison(rows, "median_ttft_seconds", "Median TTFT")


def test_positive_ttft_identifies_logarithmic_scale() -> None:
    rows = [
        {
            "profile": "cache-control",
            "prompt_id": "long",
            "clients": 1,
            "median_ttft_seconds": 2.273447621962987,
        },
        {
            "profile": "prefix-confirmed",
            "prompt_id": "long",
            "clients": 1,
            "median_ttft_seconds": 0.05172056797891855,
        },
    ]
    svg = comparison(rows, "median_ttft_seconds", "Median TTFT")
    assert metadata(svg)["scale"] == "log"
    assert "logarithmic" in accessible(svg).lower()


def test_rejection_rate_rejects_values_above_one() -> None:
    with pytest.raises(ValueError):
        comparison(
            [{"strategy": "fifo", "clients": 1, "rejection_rate": 1.01}],
            "rejection_rate",
            "Rejection rate",
        )


def test_allocation_chart_covers_every_saved_measurement() -> None:
    report = json.loads((PHASE5 / "allocation.json").read_text())
    svg = allocation_chart(report)
    text = accessible(svg).lower()
    for row in report["profiles"]:
        assert row["kv_type"].lower() in text
        assert str(row["context_per_slot"]) in text
        assert str(row["parallel"]) in text
        for value in row["allocation"]["buffer_mib"]:
            assert str(value) in text or str(int(value)) in text
    assert "mib" in text
    data = metadata(svg)
    assert (data["metric"], data["unit"], data["scale"]) == ("allocated_mib", "MiB", "linear")
    assert data["records"] == [
        {
            "kv_type": row["kv_type"],
            "context_per_slot": row["context_per_slot"],
            "parallel": row["parallel"],
            "value": sum(row["allocation"]["buffer_mib"]),
        }
        for row in report["profiles"]
    ]


@pytest.mark.parametrize(
    ("folder", "field"),
    [
        ("native-report", "median_ttft_seconds"),
        ("native-report", "mean_decode_tokens_per_second"),
        ("native-report", "throughput_tokens_per_second"),
        ("prefix-report", "median_ttft_seconds"),
        ("prefix-report", "mean_decode_tokens_per_second"),
        ("prefix-report", "throughput_tokens_per_second"),
        ("stack-report", "rejection_rate"),
        ("stack-report", "successful_requests_per_second"),
    ],
)
def test_saved_charts_expose_each_source_value(folder: str, field: str) -> None:
    summary_name = "stack-summary.json" if folder == "stack-report" else "summary.json"
    svg_name = f"stack-{field}.svg" if folder == "stack-report" else f"{field}.svg"
    rows = json.loads((PHASE5 / folder / summary_name).read_text())
    svg = (PHASE5 / folder / svg_name).read_text()
    accessible(svg)
    data = metadata(svg)
    assert data["metric"] == field
    assert data["records"] == [
        {
            **{
                key: row[key]
                for key in ("profile", "prompt_id", "clients", "strategy")
                if key in row
            },
            "value": row[field],
        }
        for row in rows
        if row[field] is not None
    ]
    expected_unit = (
        "s"
        if field == "median_ttft_seconds"
        else "%"
        if field == "rejection_rate"
        else "requests/s"
        if field == "successful_requests_per_second"
        else "tokens/s"
    )
    assert data["unit"] == expected_unit
    assert data["scale"] == ("log" if field == "median_ttft_seconds" else "linear")


def test_regeneration_is_deterministic_and_preserves_evidence(tmp_path: Path) -> None:
    destination = tmp_path / "results"
    for name in ("2026-11-08-llama-baseline", "2026-11-10-phase5"):
        shutil.copytree(RESULTS / name, destination / name)
    before = {
        path.relative_to(destination): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in destination.rglob("*")
        if path.is_file() and path.suffix != ".svg" and path.name != "curation-manifest.json"
    }
    expected = {path.relative_to(destination) for path in destination.rglob("*.svg")}
    assert len(expected) == 10
    regenerate(destination)
    first = {path: (destination / path).read_bytes() for path in expected}
    assert first == {path: (RESULTS / path).read_bytes() for path in expected}
    for data in first.values():
        accessible(data.decode())
    regenerate(destination)
    assert first == {path: (destination / path).read_bytes() for path in expected}
    assert before == {
        path.relative_to(destination): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in destination.rglob("*")
        if path.is_file() and path.suffix != ".svg" and path.name != "curation-manifest.json"
    }
    for name in ("2026-11-08-llama-baseline", "2026-11-10-phase5"):
        directory = destination / name
        manifest = json.loads((directory / "curation-manifest.json").read_text())
        for relative, digest in manifest["current_files_sha256"].items():
            assert hashlib.sha256((directory / relative).read_bytes()).hexdigest() == digest
