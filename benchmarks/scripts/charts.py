"""Publication-style SVGs from saved measurements, without executing experiments.

Regenerate all curated figures: python -m benchmarks.scripts.charts
"""

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
from xml.etree import ElementTree as ET

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import LogLocator, MaxNLocator, NullFormatter, PercentFormatter

BLUE = "#34699A"
TEAL = "#148579"
AMBER = "#C17C35"
INK = "#202C39"
GRID = "#E4E9EE"
STYLE = {
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "axes.labelsize": 11,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.edgecolor": "#A4AEB8",
    "axes.linewidth": 0.7,
    "text.color": INK,
    "axes.labelcolor": INK,
    "xtick.color": "#53606D",
    "ytick.color": INK,
    "xtick.labelsize": 10,
    "ytick.labelsize": 11,
    "svg.fonttype": "none",
    "svg.hashsalt": "llama-inference-charts",
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
}
NAMES = {
    "baseline": "Baseline · 4 threads",
    "threads-2": "2 threads",
    "threads-4": "4 threads",
    "threads-6": "6 threads",
    "context-1024": "1K context",
    "context-4096": "4K context",
    "output-32": "32 output tokens",
    "output-128": "128 output tokens",
    "q8": "Q8_0 weights",
    "kv-q8": "Q8_0 KV cache",
    "kv-q4": "Q4_0 KV cache",
    "batch-64": "Batch 64 / microbatch 32",
    "batch-512": "Batch 512 / microbatch 256",
    "flash-off": "Flash Attention off",
    "prefix-reuse": "Prefix reuse on",
    "serial-clients-2": "Serial · 2 clients",
    "serial-clients-4": "Serial · 4 clients",
    "batching-clients-2": "4 slots · 2 clients",
    "batching-clients-4": "4 slots · 4 clients",
    "no-cont-batching": "4 slots · batching off",
    "baseline-repeat": "Baseline · repeated control",
    "cache-control": "No reuse",
    "prefix-confirmed": "Prefix reuse",
    "fifo": "FIFO queue",
    "reject": "Immediate rejection",
}
METRICS = {
    "median_ttft_seconds": ("Time to first token", "Median time to first token (s)", "s"),
    "mean_decode_tokens_per_second": ("Decode speed", "Mean decode speed (tokens/s)", "tokens/s"),
    "mean_runtime_decode_tokens_per_second": (
        "CPU decode speed",
        "Mean decode speed (tokens/s)",
        "tokens/s",
    ),
    "throughput_tokens_per_second": (
        "Generation throughput",
        "Output throughput (tokens/s)",
        "tokens/s",
    ),
    "successful_requests_per_second": (
        "Request throughput",
        "Successful requests/s",
        "requests/s",
    ),
    "rejection_rate": ("Admission rejection rate", "Rejected requests (%)", "%"),
    "allocated_mib": ("KV-cache memory allocation", "Allocated KV memory (MiB)", "MiB"),
}
SVG = "http://www.w3.org/2000/svg"


def _number(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError("Chart measurements must be finite, nonnegative numbers")
    return value


def _name(value):
    return NAMES.get(value, value.replace("-", " "))


def _value(value, unit):
    if unit == "%":
        return f"{value * 100:g}%"
    if unit == "MiB":
        return f"{value:g} MiB"
    digits = 3 if unit in {"s", "requests/s"} else 2
    return f"{value:.{digits}f}" + (" s" if unit == "s" else "")


def _axes(ax, *, horizontal=True):
    ax.set_axisbelow(True)
    ax.grid(axis="x" if horizontal else "y", color=GRID, linewidth=0.7)
    for side in ("top", "right", "left" if horizontal else "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="y" if horizontal else "x", length=0, pad=7)
    ax.tick_params(axis="x" if horizontal else "y", length=3)


def _export(fig, title, description, records, field, unit, scale):
    stream = io.StringIO()
    try:
        fig.savefig(
            stream,
            format="svg",
            bbox_inches="tight",
            pad_inches=0.08,
            metadata={"Date": None, "Creator": "Llama Inference", "Title": title},
        )
    finally:
        plt.close(fig)
    root = ET.fromstring(stream.getvalue())
    root.set("role", "img")
    root.set("aria-labelledby", "chart-title chart-description")
    existing = root.find(f"{{{SVG}}}title")
    if existing is not None:
        root.remove(existing)
    ET.SubElement(root, f"{{{SVG}}}title", {"id": "chart-title"}).text = title
    ET.SubElement(root, f"{{{SVG}}}desc", {"id": "chart-description"}).text = description
    ET.SubElement(root, f"{{{SVG}}}metadata", {"id": "benchmark-data"}).text = json.dumps(
        {"metric": field, "unit": unit, "scale": scale, "records": records},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    ET.register_namespace("", SVG)
    return ET.tostring(root, encoding="unicode") + "\n"


def _legend(fig, entries):
    handles = [
        Line2D([], [], marker="o", linestyle="", markersize=6, color=color, label=name)
        for name, color in entries
    ]
    fig.legend(
        handles=handles,
        loc="upper right",
        bbox_to_anchor=(0.99, 0.99),
        ncol=len(entries),
        frameon=False,
        fontsize=10,
        handletextpad=0.4,
        columnspacing=1.2,
    )


def comparison(rows, field, label=None):
    """Render all available values, retaining exact identities in SVG metadata."""
    if field not in METRICS:
        raise ValueError(f"Unknown chart metric: {field}")
    records = [
        {
            **{k: row[k] for k in ("profile", "prompt_id", "clients", "strategy") if k in row},
            "value": _number(row[field]),
        }
        for row in rows
        if row.get(field) is not None
    ]
    if not records:
        raise ValueError("A chart requires actual measurements")
    if field == "rejection_rate" and any(r["value"] > 1 for r in records):
        raise ValueError("Rejection rates must be fractions in [0,1]")
    title, xlabel, unit = METRICS[field]
    profiles = list(dict.fromkeys(r.get("profile") for r in records))
    prefix = set(profiles) <= {"cache-control", "prefix-confirmed"}
    stack = all("strategy" in r for r in records)
    if stack:
        return _stack(records, field, title, xlabel, unit)
    groups = list(
        dict.fromkeys(r.get("prompt_id", "") if prefix else r.get("profile", "") for r in records)
    )
    series = list(
        dict.fromkeys(r.get("profile") if prefix else r.get("prompt_id", "") for r in records)
    )
    colors = {series[0]: BLUE, **{name: TEAL for name in series[1:]}}
    log = field == "median_ttft_seconds" and all(r["value"] > 0 for r in records)
    title = "Prefix caching: time to first token" if prefix and log else title
    height = max(2.55, 1.35 + len(groups) * (0.49 if log else 0.46))
    with mpl.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(8.6, height), layout="constrained")
        fig.suptitle(title, x=0.01, ha="left", fontsize=15, fontweight="bold")
        _axes(ax)
        if log:
            ax.set_xscale("log")
            low = min(r["value"] for r in records)
            high = max(r["value"] for r in records)
            ax.set_xlim(low / 1.7, high * 1.7)
            ax.xaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5)))
            ax.xaxis.set_major_formatter(lambda x, _: f"{x:g}")
            ax.xaxis.set_minor_formatter(NullFormatter())
        else:
            ax.set_xlim(0, max(max(r["value"] for r in records) * 1.30, 1))
            ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
        for index, group in enumerate(groups):
            selected = [
                r
                for r in records
                if (r.get("prompt_id", "") if prefix else r.get("profile", "")) == group
            ]
            if log and len(selected) > 1:
                ax.plot(
                    [min(r["value"] for r in selected), max(r["value"] for r in selected)],
                    [index, index],
                    color="#CBD3DA",
                    linewidth=2.3,
                    zorder=1,
                )
            for record in selected:
                name = record.get("profile") if prefix else record.get("prompt_id", "")
                j = series.index(name)
                value = record["value"]
                if log:
                    ax.scatter(
                        value,
                        index,
                        color=colors[name],
                        s=52,
                        zorder=3,
                        edgecolor="white",
                        linewidth=0.7,
                    )
                    ax.annotate(
                        _value(value, unit),
                        (value, index),
                        xytext=(0, 7 if j == 0 else -7),
                        textcoords="offset points",
                        ha="center",
                        va="bottom" if j == 0 else "top",
                        fontsize=10,
                        color=colors[name],
                        fontweight="medium",
                    )
                else:
                    offset = (j - (len(series) - 1) / 2) * 0.38
                    ax.barh(index + offset, value, height=0.32, color=colors[name], zorder=2)
                    ax.annotate(
                        _value(value, unit),
                        (value, index + offset),
                        xytext=(5, 0),
                        textcoords="offset points",
                        va="center",
                        fontsize=10,
                    )
        ax.set_yticks(
            range(len(groups)), [f"{g.title()} prompt" if prefix else _name(g) for g in groups]
        )
        ax.set_ylim(len(groups) - 0.45, -0.58)
        ax.set_xlabel(xlabel + (" · logarithmic scale" if log else ""), labelpad=9)
        names = [(NAMES.get(s, s.title() + " prompt"), colors[s]) for s in series]
        _legend(fig, names)
        subtitle = (
            "Warm repeated prompts; uncached control"
            if prefix
            else "Recorded point estimates · warm-ups excluded"
        )
        if label and ("Llama" in label or "Q4" in label or "Q8" in label):
            subtitle = label.replace("meta-llama/", "")
        ax.set_title(
            subtitle, loc="left", fontsize=10, fontweight="normal", color="#5D6976", pad=13
        )
        fig.get_layout_engine().set(h_pad=0.10, w_pad=0.08)
        description = (
            f"{title}. {xlabel}. {'Logarithmic' if log else 'Zero-based linear'} axis. "
            "Warm-ups excluded. Exact source measurements and configuration identities "
            "are embedded in benchmark-data metadata."
        )
        return _export(fig, title, description, records, field, unit, "log" if log else "linear")


def _stack(records, field, title, xlabel, unit):
    groups = list(dict.fromkeys(r["clients"] for r in records))
    series = list(dict.fromkeys(r["strategy"] for r in records))
    colors = {"fifo": BLUE, "reject": AMBER}
    with mpl.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(7.4, 3.35), layout="constrained")
        fig.suptitle(title, x=0.01, ha="left", fontsize=15, fontweight="bold")
        _axes(ax, horizontal=False)
        maximum = 1 if field == "rejection_rate" else (max(r["value"] for r in records) * 1.27 or 1)
        ax.set_ylim(0, maximum)
        if field == "rejection_rate":
            ax.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
        else:
            ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
        for j, name in enumerate(series):
            for i, group in enumerate(groups):
                r = next(
                    (r for r in records if r["clients"] == group and r["strategy"] == name), None
                )
                if r is None:
                    continue
                x = i + (j - (len(series) - 1) / 2) * 0.31
                ax.bar(x, r["value"], width=0.27, color=colors.get(name, TEAL))
                ax.annotate(
                    _value(r["value"], unit),
                    (x, r["value"]),
                    xytext=(0, 6),
                    textcoords="offset points",
                    ha="center",
                    fontsize=10,
                )
        ax.set_xticks(range(len(groups)), [str(g) for g in groups])
        ax.set_xlabel("Concurrent clients", labelpad=7)
        ax.set_ylabel(xlabel, labelpad=7)
        ax.set_title(
            "One active generation · three measured waves per setting",
            loc="left",
            fontsize=10,
            color="#5D6976",
            fontweight="normal",
            pad=13,
        )
        _legend(fig, [(_name(s), colors.get(s, TEAL)) for s in series])
        return _export(
            fig,
            title,
            f"{title}. {xlabel}. Linear zero-based axis; rejection uses all attempted requests. "
            "Values grouped by client count and admission strategy.",
            records,
            field,
            unit,
            "linear",
        )


def allocation_chart(report):
    """Separate precision changes from context/slot capacity, retaining all probes."""
    records = []
    for row in report["profiles"]:
        values = row["allocation"]["buffer_mib"]
        if not values:
            raise ValueError("Allocation chart requires runtime buffer measurements")
        records.append(
            {
                "kv_type": row["kv_type"],
                "context_per_slot": row["context_per_slot"],
                "parallel": row["parallel"],
                "value": sum(_number(v) for v in values),
            }
        )
    precision = [r for r in records if r["context_per_slot"] == 2048 and r["parallel"] == 1]
    capacity = [r for r in records if r["kv_type"] == "f16"]
    if not precision or not capacity:
        raise ValueError("Require precision and FP16 capacity probes")
    title = "KV-cache memory allocation"
    with mpl.rc_context(STYLE):
        fig, axes = plt.subplots(1, 2, figsize=(9.0, 3.45), layout="constrained")
        fig.suptitle(title, x=0.01, ha="left", fontsize=15, fontweight="bold")
        for ax, data, heading, subtitle in zip(
            axes,
            (precision, capacity),
            ("Cache precision", "FP16 capacity scaling"),
            ("2,048 tokens · one slot", "Context per slot × active capacity"),
            strict=True,
        ):
            _axes(ax)
            maximum = max(r["value"] for r in data)
            ax.set_xlim(0, maximum * 1.32)
            ax.xaxis.set_major_locator(MaxNLocator(nbins=4))
            for i, r in enumerate(data):
                color = {"f16": BLUE, "q8_0": "#48A5A0", "q4_0": TEAL}[r["kv_type"]]
                ax.barh(i, r["value"], height=0.52, color=color)
                ax.annotate(
                    _value(r["value"], "MiB"),
                    (r["value"], i),
                    xytext=(5, 0),
                    textcoords="offset points",
                    va="center",
                    fontsize=11,
                )
            labels = (
                [r["kv_type"].upper().replace("F16", "FP16") for r in data]
                if ax is axes[0]
                else [
                    f"{r['context_per_slot'] // 1024}K · {r['parallel']} "
                    f"{'slot' if r['parallel'] == 1 else 'slots'}"
                    for r in data
                ]
            )
            ax.set_yticks(range(len(data)), labels)
            ax.set_ylim(len(data) - 0.45, -0.7)
            ax.set_xlabel("KV memory (MiB)", labelpad=8)
            ax.set_title(heading, loc="left", pad=24)
            ax.text(0, 1.04, subtitle, transform=ax.transAxes, fontsize=10, color="#5D6976")
        description = (
            "Measured runtime KV-buffer allocation in MiB. Left: compare cache precision "
            "at 2,048 tokens and one slot. Right: FP16 context and slot capacity; the shared "
            "64 MiB reference is repeated. Panels use separately labeled, zero-based "
            "linear axes. Process RSS is not shown."
        )
        return _export(fig, title, description, records, "allocated_mib", "MiB", "linear")


def regenerate(results):
    """Rewrite SVGs and their curation checksums only; never write result tables."""
    from benchmarks.scripts.metrics import summarize as baseline_summary
    from benchmarks.scripts.report import render_chart
    from benchmarks.scripts.report_experiments import summarize

    results = Path(results)
    outputs = {}
    for raw in sorted(results.glob("*/raw.json")):
        report = json.loads(raw.read_text())
        if report.get("status") != "complete":
            raise ValueError("Require completed baseline measurements")
        model = report["artifacts"]["model"]
        outputs[raw.parent / "decode-speed.svg"] = render_chart(
            baseline_summary(report["requests"]), f"{model['upstream']} · {model['quantization']}"
        )
    for directory in sorted(results.iterdir()):
        if not directory.is_dir():
            continue
        for stem in ("native", "prefix"):
            raw = directory / f"{stem}.json"
            if raw.exists():
                report = json.loads(raw.read_text())
                if report.get("status") not in {"complete", "complete_with_failures"}:
                    raise ValueError("Require finished native measurements")
                rows = summarize(report)
                for field in (
                    "median_ttft_seconds",
                    "mean_decode_tokens_per_second",
                    "throughput_tokens_per_second",
                ):
                    outputs[directory / f"{stem}-report" / f"{field}.svg"] = comparison(rows, field)
        allocation = directory / "allocation.json"
        if allocation.exists():
            outputs[directory / "kv-allocation.svg"] = allocation_chart(
                json.loads(allocation.read_text())
            )
        stack = directory / "stack.json"
        if stack.exists():
            rows = json.loads(stack.read_text())["summary"]
            for field in ("rejection_rate", "successful_requests_per_second"):
                outputs[directory / "stack-report" / f"stack-{field}.svg"] = comparison(rows, field)
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    for manifest in results.glob("*/curation-manifest.json"):
        data = json.loads(manifest.read_text())
        verification = manifest.parent / "verification.json"
        historical = (
            json.loads(verification.read_text()).get("result_sha256", {})
            if verification.exists()
            else {}
        )
        for path in outputs:
            if path.is_relative_to(manifest.parent):
                name = str(path.relative_to(manifest.parent))
                if name in data["current_files_sha256"]:
                    data["current_files_sha256"][name] = hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if name in historical and digest != historical[name]:
                    data["differences_from_measurement_time_result_tree"][name] = {
                        "measurement_time_sha256": historical[name],
                        "current_sha256": digest,
                    }
                elif name in data["differences_from_measurement_time_result_tree"]:
                    data["differences_from_measurement_time_result_tree"][name][
                        "current_sha256"
                    ] = digest
        manifest.write_text(json.dumps(data, indent=2) + "\n")
    return list(outputs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results", type=Path, default=Path(__file__).resolve().parents[1] / "results"
    )
    args = parser.parse_args()
    outputs = regenerate(args.results)
    print(f"Regenerated {len(outputs)} SVG charts from saved measurements.")


if __name__ == "__main__":
    main()
