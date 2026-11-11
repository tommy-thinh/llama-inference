import json
from pathlib import Path
from xml.etree.ElementTree import fromstring

import pytest

from benchmarks.scripts.metrics import summarize
from benchmarks.scripts.report import generate_report, render_chart, render_table
from inference.artifacts import ROOT

LLAMA_SHA256 = "6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83"


def test_report_preserves_measured_values_and_produces_valid_svg() -> None:
    rows = [
        {
            "profile": "threads-4",
            "prompt_id": "short",
            "requests": 3,
            "median_ttft_seconds": 0.125,
            "median_total_seconds": 2.5,
            "mean_runtime_decode_tokens_per_second": 12.34,
            "peak_server_rss_bytes": 2**30,
            "mean_server_cpu_percent": 156.2,
        }
    ]
    table = render_table(rows)
    assert "Server CPU mean (% of one logical CPU)" in table
    assert "0.125 | 2.500 | 12.34 | 156.2 | 1024.0" in table
    assert "156.2" in table
    root = fromstring(render_chart(rows))
    assert root.attrib["role"] == "img"
    labels = " ".join(root.itertext())
    metadata = root.find("{http://www.w3.org/2000/svg}metadata[@id='benchmark-data']")
    assert metadata is not None and metadata.text
    assert json.loads(metadata.text)["records"] == [
        {"profile": "threads-4", "prompt_id": "short", "value": 12.34}
    ]
    assert "12.34" in labels


def test_chart_preserves_a_valid_zero_decode_measurement() -> None:
    rows = [
        {
            "profile": "one-token",
            "prompt_id": "arithmetic",
            "mean_runtime_decode_tokens_per_second": 0.0,
        }
    ]
    chart = fromstring(render_chart(rows))
    text = " ".join(chart.itertext())
    assert "one-token" in text
    assert "arithmetic" in text
    assert "0.00" in text


def test_failed_run_cannot_be_presented_as_complete(tmp_path: Path) -> None:
    path = tmp_path / "failed.json"
    path.write_text(json.dumps({"schema_version": 1, "status": "failed"}))
    with pytest.raises(ValueError, match="completed"):
        generate_report(path, tmp_path / "reports")
    assert not (tmp_path / "reports").exists()


def test_current_report_identifies_llama_and_its_quantization(tmp_path: Path) -> None:
    raw_path = tmp_path / "llama.json"
    raw_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "complete",
                "artifacts": {
                    "model": {
                        "upstream": "meta-llama/Llama-3.2-1B-Instruct",
                        "filename": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
                        "size_bytes": 807_694_464,
                        "sha256": LLAMA_SHA256,
                        "quantization": "Q4_K_M",
                    }
                },
                "requests": [
                    {
                        "profile": "baseline",
                        "prompt_id": "short",
                        "phase": "measured",
                        "ttft_seconds": 0.25,
                        "total_seconds": 1.5,
                        "completion_tokens": 10,
                        "finish_reason": "stop",
                        "effective_tokens_per_second": 10 / 1.5,
                        "timings": {"predicted_per_second": 12.0},
                        "memory": {"peak_rss_bytes": 1024},
                        "server_cpu_time_seconds": 0.75,
                        "server_cpu_percent": 50.0,
                    }
                ],
                "runtime_commit": "620d2225cc436ab0b001e3200e590e3b286ef9ab",
            }
        )
    )
    output_directory = tmp_path / "llama-report"
    generate_report(raw_path, output_directory)
    chart_text = (output_directory / "decode-speed.svg").read_text()
    summary_text = (output_directory / "summary.md").read_text()
    assert "Llama" in chart_text
    assert "Q4_K_M" in chart_text
    assert "Q8_0" not in chart_text
    assert "Server CPU mean (% of one logical CPU)" in summary_text
    assert "one logical CPU" in summary_text
    assert "| 50.0 |" in summary_text


def test_curated_baseline_evidence_is_consistent(tmp_path: Path) -> None:
    report = json.loads(
        (ROOT / "benchmarks/results/2026-11-08-llama-baseline/raw.json").read_text()
    )
    model = report["artifacts"]["model"]
    assert model["upstream"] == "meta-llama/Llama-3.2-1B-Instruct"
    assert model["sha256"] == LLAMA_SHA256
    assert report["verified_model_sha256"] == model["sha256"]
    assert report["status"] == "complete"
    profiles = report["configuration"]["profiles"]
    prompts = report["configuration"]["prompts"]
    measured = [row for row in report["requests"] if row["phase"] == "measured"]
    warmups = [row for row in report["requests"] if row["phase"] == "warmup"]
    assert len(measured) == len(profiles) * len(prompts) * report["configuration"]["repetitions"]
    assert len(warmups) == len(profiles) * len(prompts) * report["configuration"]["warmups"]
    for row in report["requests"]:
        assert 0 < row["ttft_seconds"] <= row["total_seconds"]
        assert 0 < row["completion_tokens"] <= row["payload"]["max_tokens"]
        assert row["timings"]["cache_n"] == 0
        assert row["timings"]["predicted_per_second"] > 0
    assert all(profile["server_exit_code"] == 0 for profile in report["profiles"])
    assert report["verified_model_sha256"] == report["artifacts"]["model"]["sha256"]
    assert report["runtime_commit"] == report["artifacts"]["runtime"]["commit"]
    recomputed = summarize(report["requests"])
    assert len(report["summary"]) == len(recomputed)
    for saved, current in zip(report["summary"], recomputed, strict=True):
        assert all(current[key] == value for key, value in saved.items())
    q8 = json.loads((ROOT / "benchmarks/artifacts.lock.json").read_text())["models"]["q8"]
    report["artifacts"]["model"] = q8
    q8_raw = tmp_path / "q8.json"
    q8_raw.write_text(json.dumps(report))
    generate_report(q8_raw, tmp_path / "q8-report")
    assert "Q8_0" in (tmp_path / "q8-report/decode-speed.svg").read_text()
    assert q8["sha256"] in (tmp_path / "q8-report/summary.md").read_text()
