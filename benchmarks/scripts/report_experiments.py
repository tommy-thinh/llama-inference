"""Tables and standalone SVG charts computed from saved Phase 5 measurements."""

import argparse
import json
import math
from pathlib import Path
from statistics import mean, median
from xml.etree.ElementTree import Element, SubElement, tostring


def percentile(values, quantile=0.95):
    if not values or not 0 < quantile <= 1:
        raise ValueError("Percentile requires samples and quantile in (0,1]")
    if any(
        not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0
        for v in values
    ):
        raise ValueError("Samples must be finite nonnegative numbers")
    return sorted(values)[math.ceil(len(values) * quantile) - 1]


def summarize(report):
    groups = {}
    for row in report["requests"]:
        if row["phase"] == "measured":
            groups.setdefault((row["profile"], row["prompt_id"], row["clients"]), []).append(row)
    result = []
    for (profile, prompt, clients), rows in groups.items():
        good = [row for row in rows if row["success"]]
        waves = [
            wave
            for wave in report["waves"]
            if wave["phase"] == "measured"
            and (wave["profile"], wave["prompt_id"], wave["clients"]) == (profile, prompt, clients)
        ]
        seconds = sum(wave["wall_seconds"] for wave in waves)
        counts = [row["completion_tokens"] for row in good]
        speeds = [
            row["timings"]["predicted_per_second"]
            for row in good
            if "predicted_per_second" in row["timings"]
        ]
        cache = [row["timings"]["cache_n"] for row in good if "cache_n" in row["timings"]]
        result.append(
            {
                "profile": profile,
                "prompt_id": prompt,
                "clients": clients,
                "attempts": len(rows),
                "successes": len(good),
                "errors": len(rows) - len(good),
                "error_rate": (len(rows) - len(good)) / len(rows),
                "mean_latency_seconds": mean(row["total_seconds"] for row in good)
                if good
                else None,
                "median_latency_seconds": median(row["total_seconds"] for row in good)
                if good
                else None,
                "p95_latency_seconds": percentile([row["total_seconds"] for row in good])
                if good
                else None,
                "median_ttft_seconds": median(row["ttft_seconds"] for row in good)
                if good
                else None,
                "mean_decode_tokens_per_second": mean(speeds) if speeds else None,
                "throughput_requests_per_second": len(good) / seconds if seconds else None,
                "throughput_tokens_per_second": sum(counts) / seconds if seconds else None,
                "completion_tokens": counts,
                "mean_cache_tokens": mean(cache) if cache else None,
                "cache_counter_samples": len(cache),
                "mean_cpu_percent": mean(
                    wave["server_cpu_percent"]
                    for wave in waves
                    if wave["server_cpu_percent"] is not None
                )
                if any(wave["server_cpu_percent"] is not None for wave in waves)
                else None,
                "peak_rss_bytes": max(
                    (wave["memory"].get("peak_rss_bytes", 0) for wave in waves), default=0
                ),
            }
        )
    return result


def chart(rows, field, label):
    data = [row for row in rows if row.get(field) is not None]
    root = Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "viewBox": f"0 0 1060 {100 + len(data) * 28}",
            "role": "img",
        },
    )
    SubElement(root, "title").text = label
    SubElement(root, "rect", {"width": "100%", "height": "100%", "fill": "white"})
    SubElement(
        root, "text", {"x": "20", "y": "30", "font-family": "sans-serif", "font-size": "19"}
    ).text = label
    maximum = max([float(row[field]) for row in data] + [1e-9])
    for index, row in enumerate(data):
        y = 65 + index * 28
        name = (
            row.get("profile", row.get("strategy", ""))
            + " / "
            + row.get("prompt_id", str(row.get("clients", "")))
        )
        SubElement(
            root,
            "text",
            {"x": "12", "y": str(y + 14), "font-family": "sans-serif", "font-size": "12"},
        ).text = name
        length = 650 * row[field] / maximum
        SubElement(
            root,
            "rect",
            {"x": "295", "y": str(y), "width": str(length), "height": "19", "fill": "#2563eb"},
        )
        SubElement(
            root,
            "text",
            {
                "x": str(300 + length),
                "y": str(y + 14),
                "font-family": "sans-serif",
                "font-size": "12",
            },
        ).text = f"{row[field]:.3f}"
    return tostring(root, encoding="unicode") + "\n"


def fmt(value):
    return f"{value:.3f}" if isinstance(value, (int, float)) else "unavailable"


def generate(raw_path, output_dir):
    report = json.loads(Path(raw_path).read_text())
    if (
        report.get("schema_version") != 2
        or report.get("kind") != "native"
        or report.get("status") not in {"complete", "complete_with_failures"}
    ):
        raise ValueError("Require a finished native Phase5 report")
    rows = summarize(report)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(json.dumps(rows, indent=2, allow_nan=False) + "\n")
    lines = [
        "# Native optimization measurements",
        "",
        "Warm-ups excluded. p95 uses nearest rank; small samples are descriptive only.",
        "",
        "| Profile | Prompt | Clients | Success/attempt | TTFT s | Median latency s | p95 s "
        "| Decode tok/s | Throughput tok/s | CPU % | Peak RSS MiB | Cached tokens |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        fields = (
            [
                row["profile"],
                row["prompt_id"],
                str(row["clients"]),
                f"{row['successes']}/{row['attempts']}",
            ]
            + [
                fmt(row[key])
                for key in (
                    "median_ttft_seconds",
                    "median_latency_seconds",
                    "p95_latency_seconds",
                    "mean_decode_tokens_per_second",
                    "throughput_tokens_per_second",
                    "mean_cpu_percent",
                )
            ]
            + [fmt(row["peak_rss_bytes"] / 2**20), fmt(row["mean_cache_tokens"])]
        )
        lines.append("| " + " | ".join(fields) + " |")
    (directory / "summary.md").write_text("\n".join(lines) + "\n")
    for field in (
        "median_ttft_seconds",
        "mean_decode_tokens_per_second",
        "throughput_tokens_per_second",
    ):
        (directory / (field + ".svg")).write_text(chart(rows, field, field.replace("_", " ")))
    quality = {}
    for row in report["quality"]:
        group = quality.setdefault(row["model"], {"passed": 0, "cases": 0})
        group["passed"] += int(row["passed"])
        group["cases"] += 1
    (directory / "quality-summary.json").write_text(json.dumps(quality, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    generate(args.raw, args.output_dir)


if __name__ == "__main__":
    main()
