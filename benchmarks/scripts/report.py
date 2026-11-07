"""Generate a Markdown table and a standalone SVG from completed benchmark results."""

import argparse
import json
import math
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

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
    width, left, top, step, plot_width = 1000, 235, 100, 30, 650
    height = top + len(rows) * step + 60
    root = Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "viewBox": f"0 0 {width} {height}",
            "role": "img",
            "aria-labelledby": "title description",
        },
    )
    SubElement(root, "title", {"id": "title"}).text = "CPU baseline: runtime decode speed"
    SubElement(root, "desc", {"id": "description"}).text = (
        "Mean tokens per second for each configuration and prompt. Warm-up requests are excluded. "
        "This chart measures decode speed, not end-to-end request throughput."
    )
    SubElement(root, "rect", {"width": str(width), "height": str(height), "fill": "#ffffff"})

    def label(x: float, y: float, value: str, size: int = 14, **attributes: str) -> None:
        SubElement(
            root,
            "text",
            {
                "x": str(x),
                "y": str(y),
                "fill": "#172033",
                "font-family": "sans-serif",
                "font-size": str(size),
                **attributes,
            },
        ).text = value

    label(24, 32, model_label, 22)
    label(24, 58, "Runtime decode tokens/s (mean); serial requests; warm-ups excluded", 14)
    maximum = max(
        5, math.ceil(max(row["mean_runtime_decode_tokens_per_second"] for row in rows) / 5) * 5
    )
    for tick in range(0, maximum + 1, 5):
        x = left + tick / maximum * plot_width
        SubElement(
            root,
            "line",
            {
                "x1": str(x),
                "x2": str(x),
                "y1": str(top - 15),
                "y2": str(height - 50),
                "stroke": "#e2e8f0",
            },
        )
        label(x, height - 24, str(tick), **{"text-anchor": "middle"})
    for index, row in enumerate(rows):
        y = top + index * step
        speed = row["mean_runtime_decode_tokens_per_second"]
        label(left - 12, y + 14, f"{row['profile']} / {row['prompt_id']}", **{"text-anchor": "end"})
        SubElement(
            root,
            "rect",
            {
                "x": str(left),
                "y": str(y),
                "width": str(speed / maximum * plot_width),
                "height": "20",
                "rx": "3",
                "fill": "#2563eb" if row["prompt_id"] == "short" else "#d97706",
            },
        )
        label(left + speed / maximum * plot_width + 6, y + 15, f"{speed:.2f}")
    return tostring(root, encoding="unicode") + "\n"


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
