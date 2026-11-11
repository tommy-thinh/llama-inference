"""Generate a Markdown table and a standalone SVG from completed benchmark results."""

import argparse
import json
from pathlib import Path
from typing import Any

from benchmarks.scripts.charts import comparison
from benchmarks.scripts.metrics import summarize


def render_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Profile | Prompt | Runs | TTFT median (s) | Total median (s) | Decode mean (tok/s) "
        "| Server CPU mean (% of one logical CPU) | Peak RSS (MiB) |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        speed = row["mean_runtime_decode_tokens_per_second"]
        label = f"{speed:.2f}" if speed is not None else "unavailable"
        cpu = row.get("mean_server_cpu_percent")
        cpu_label = f"{cpu:.1f}" if cpu is not None else "unavailable"
        lines.append(
            f"| {row['profile']} | {row['prompt_id']} | {row['requests']} "
            f"| {row['median_ttft_seconds']:.3f} | {row['median_total_seconds']:.3f} "
            f"| {label} | {cpu_label} | {row['peak_server_rss_bytes'] / 2**20:.1f} |"
        )
    return "\n".join(lines) + "\n"


def render_chart(
    rows: list[dict[str, Any]], model_label: str = "Llama 3.2 1B Instruct · Q4_K_M · CPU baseline"
) -> str:
    if not rows or any(row["mean_runtime_decode_tokens_per_second"] is None for row in rows):
        raise ValueError("A chart requires actual runtime decode measurements")
    return comparison(rows, "mean_runtime_decode_tokens_per_second", model_label)


def generate_report(raw_path: Path, output_directory: Path) -> None:
    report = json.loads(raw_path.read_text())
    if report.get("schema_version") != 1 or report.get("status") != "complete":
        raise ValueError("Only completed schema v1 experiments can be summarized")
    rows = summarize(report["requests"])
    output_directory.mkdir(parents=True, exist_ok=True)
    model = report["artifacts"]["model"]
    summary_header = (
        "# Inference baseline summary\n\n"
        f"- Model: `{model['upstream']}`\n"
        f"- GGUF: `{model['filename']}` ({model['size_bytes']:,} bytes)\n"
        f"- Quantization: `{model['quantization']}`\n"
        f"- Model SHA-256: `{model['sha256']}`\n"
        f"- llama.cpp commit: `{report['runtime_commit']}`\n"
        "- CPU measurement: server process user+system time during each request, "
        "as percent of one logical CPU (100% equals one fully occupied CPU).\n\n"
    )
    for name, content in (
        ("summary.md", summary_header + render_table(rows)),
        (
            "decode-speed.svg",
            render_chart(rows, f"{model['upstream']} · {model['quantization']} · CPU baseline"),
        ),
    ):
        (output_directory / name).write_text(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    generate_report(args.raw, args.output_dir)


if __name__ == "__main__":
    main()
