"""Render the README / gallery figures from a recorded run.

    python scripts/render_figures.py docs/sample-run/run.json docs/sample-terminal.txt

Writes docs/coverage.png, docs/pipeline.png and docs/terminal.png. Needs matplotlib
(``pip install -e .[figures]``); the agent itself does not.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

DOCS = Path(__file__).resolve().parents[1] / "docs"
INK = "#1f2933"
MUTED = "#7b8794"
GREEN = "#76b900"  # accent
BLUE = "#3e6fb0"
RED = "#c2410c"
BG = "#ffffff"


def coverage_chart(run: dict, out: Path) -> None:
    rounds = run["rounds"]
    labels = ["existing\ntests"] + [f"round {r['index']}" for r in rounds] + ["final"]
    values = [run["baseline"]["percent"]] + [r["coverage"] or 0 for r in rounds] + [run["final"]["percent"]]
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(10, 4.2), gridspec_kw={"width_ratios": [1.7, 1]}, facecolor=BG)
    colors = [MUTED] + [BLUE] * len(rounds) + [GREEN]
    bars = ax.bar(labels, values, color=colors, width=0.62)
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.0f}%", ha="center", color=INK, fontsize=10)
    ax.set_ylim(0, 112)
    ax.set_ylabel("line coverage of buggy_inventory", color=INK)
    ax.set_title("Coverage through the loop", color=INK, loc="left", fontsize=12)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)

    counts = [
        ("written", run["test_count_written"], BLUE),
        ("repaired", len(run["repaired"]), MUTED),
        ("kept, passing", len(run["kept_passing"]), GREEN),
        ("kept, bug found", len(run["bugs"]), RED),
        ("dropped", len(run["dropped"]), MUTED),
    ]
    names = [c[0] for c in counts][::-1]
    nums = [c[1] for c in counts][::-1]
    ax2.barh(names, nums, color=[c[2] for c in counts][::-1], height=0.6)
    for i, n in enumerate(nums):
        ax2.text(n + 0.3, i, str(n), va="center", color=INK, fontsize=10)
    ax2.set_title("Test functions", color=INK, loc="left", fontsize=12)
    ax2.set_xlim(0, max(nums) + 4)
    for s in ("top", "right"):
        ax2.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def pipeline(out: Path) -> None:
    fig, ax = plt.subplots(figsize=(11, 3.6), facecolor=BG)
    ax.set_xlim(0, 11)
    ax.set_ylim(-0.3, 3.6)
    ax.axis("off")
    steps = [
        (0.2, "Read", "package source,\ndocstrings, API"),
        (2.3, "Write", "Nemotron 3.5 Lightning\nwrites pytest tests"),
        (4.4, "Run", "temp-dir copy, timeout,\nno network, coverage.py"),
        (6.5, "Triage", "Nemotron 3 Ultra:\nbad test or real bug?"),
        (8.6, "Keep", "passing tests +\nstrict-xfail bug tests"),
    ]
    for x, title, body in steps:
        color = GREEN if title in ("Write", "Triage") else BLUE
        ax.add_patch(FancyBboxPatch((x, 1.4), 1.9, 1.5, boxstyle="round,pad=0.04,rounding_size=0.12", fc="white", ec=color, lw=2))
        ax.text(x + 0.95, 2.55, title, ha="center", va="center", fontsize=13, weight="bold", color=INK)
        ax.text(x + 0.95, 1.9, body, ha="center", va="center", fontsize=8.6, color=INK)
    for x in (2.1, 4.2, 6.3, 8.4):
        ax.add_patch(FancyArrowPatch((x, 2.15), (x + 0.2, 2.15), arrowstyle="-|>", mutation_scale=14, color=INK))
    ax.add_patch(
        FancyArrowPatch((7.45, 1.38), (3.25, 1.38), connectionstyle="arc3,rad=-0.22", arrowstyle="-|>", mutation_scale=14, color=RED, lw=1.6)
    )
    ax.text(5.35, 0.05, "test was wrong: repair it and run again (up to N rounds)\ncoverage below target: ask for tests on the uncovered lines",
            ha="center", fontsize=9, color=RED)
    ax.text(0.2, 3.35, "testsmith: every model call goes to Nebius Token Factory (OpenAI-compatible API)", fontsize=10, color=MUTED)
    fig.savefig(out, dpi=160, bbox_inches="tight")
    plt.close(fig)


def terminal(text: str, out: Path) -> None:
    lines = text.rstrip().splitlines()
    fig = plt.figure(figsize=(12, 0.24 * len(lines) + 0.8), facecolor="#111418")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    ax.set_facecolor("#111418")
    y = 1 - 0.4 / (0.24 * len(lines) + 0.8)
    step = 0.24 / (0.24 * len(lines) + 0.8)
    ax.text(0.015, y + step * 0.2, "$ testsmith examples/buggy_inventory --replay examples/replay/buggy_inventory.json --out docs/sample-run", family="monospace", fontsize=9.5, color=GREEN, va="top")
    for i, line in enumerate(lines, 1):
        color = "#e4e7eb"
        if line.lstrip().startswith("BUG"):
            color = "#f59e0b"
        elif line.lstrip().startswith("[model]"):
            color = "#9aa5b1"
        elif line.startswith("["):
            color = "#7cc4fa"
        ax.text(0.015, y - i * step + step * 0.2, line, family="monospace", fontsize=8.6, color=color, va="top")
    fig.savefig(out, dpi=150, facecolor="#111418")
    plt.close(fig)


def main() -> None:
    run = json.loads(Path(sys.argv[1]).read_text())
    term = Path(sys.argv[2]).read_text()
    coverage_chart(run, DOCS / "coverage.png")
    pipeline(DOCS / "pipeline.png")
    terminal(term, DOCS / "terminal.png")
    print("wrote docs/coverage.png, docs/pipeline.png, docs/terminal.png")


if __name__ == "__main__":
    main()
