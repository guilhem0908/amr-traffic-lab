"""Figures drawn from the committed result tables (requires matplotlib)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .experiments import saturation_fleet
from .layout import LAYOUT_NAMES

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
GRID = "#e4e3df"
SERIES_COLOUR = {"reservation": "#2a78d6", "naive": "#eb6834", "cbs": "#1baf7a"}
SERIES_LABEL = {
    "reservation": "Reservation manager (space-time A* + reservation table)",
    "naive": "Naive manager (independent A* + wait)",
}
LAYOUT_TITLE = {
    "open_floor": "Open floor",
    "narrow_aisles": "Narrow aisles",
    "single_corridor": "Single corridor",
}
LINE_WIDTH = 1.6
MARKER_SIZE = 6.5
BAND_ALPHA = 0.12
HEADROOM = 1.12
"""Top of the throughput axis as a multiple of the highest value, to leave room for labels."""
DPI = 160


def _style(axis) -> None:
    axis.set_facecolor(SURFACE)
    axis.grid(axis="y", color=GRID, linewidth=0.8)
    axis.set_axisbelow(True)
    for side in ("top", "right", "left"):
        axis.spines[side].set_visible(False)
    axis.spines["bottom"].set_color(GRID)
    axis.tick_params(colors=INK_SECONDARY, length=0, labelsize=9)


def _line(axis, x, y, colour, label=None) -> None:
    axis.plot(
        x,
        y,
        color=colour,
        linewidth=LINE_WIDTH,
        marker="o",
        markersize=MARKER_SIZE,
        markeredgecolor=SURFACE,
        markeredgewidth=1.4,
        label=label,
        solid_capstyle="round",
        clip_on=False,
        zorder=3,
    )


def plot_lifelong(summary: Sequence[dict], path: Path) -> None:
    """Throughput (top) and gridlocked runs (bottom) versus fleet size, one column per layout."""
    figure, axes = plt.subplots(
        2,
        len(LAYOUT_NAMES),
        figsize=(10.5, 5.9),
        sharex=True,
        sharey="row",
        facecolor=SURFACE,
        gridspec_kw={"height_ratios": [2.1, 1]},
    )
    runs = int(summary[0]["runs"])
    for column, layout in enumerate(LAYOUT_NAMES):
        top, bottom = axes[0][column], axes[1][column]
        for manager in ("reservation", "naive"):
            rows = [r for r in summary if r["layout"] == layout and r["manager"] == manager]
            fleet = [int(r["robots"]) for r in rows]
            mean = [float(r["throughput_mean"]) for r in rows]
            colour = SERIES_COLOUR[manager]
            top.fill_between(
                fleet,
                [float(r["throughput_min"]) for r in rows],
                [float(r["throughput_max"]) for r in rows],
                color=colour,
                alpha=BAND_ALPHA,
                linewidth=0,
            )
            _line(top, fleet, mean, colour, SERIES_LABEL[manager] if column == 0 else None)
            top.annotate(
                f"{mean[-1]:.0f}",
                (fleet[-1], mean[-1]),
                xytext=(7, 0),
                textcoords="offset points",
                va="center",
                fontsize=9,
                color=INK,
            )
            _line(bottom, fleet, [int(r["gridlocked_runs"]) for r in rows], colour)
        knee = saturation_fleet(summary, layout, "reservation")
        top.annotate(
            f"95% of best\nat {knee['robots']} robots",
            (int(knee["robots"]), float(knee["throughput_mean"])),
            xytext=(-16, 24),
            textcoords="offset points",
            ha="center",
            fontsize=8.5,
            color=INK_SECONDARY,
            arrowprops={"arrowstyle": "-", "color": INK_SECONDARY, "linewidth": 0.8},
        )
        top.set_title(LAYOUT_TITLE[layout], fontsize=11, color=INK, loc="left", pad=8)
        bottom.set_xlabel("robots in the fleet", fontsize=9.5, color=INK_SECONDARY)
        bottom.set_xticks([1, 4, 8, 12, 16])
        bottom.set_ylim(-1, runs + 1)
        bottom.set_yticks(range(0, runs + 1, runs // 4))
        top.set_xlim(0.2, 17.8)
        _style(top)
        _style(bottom)
    highest = max(float(r["throughput_max"]) for r in summary)
    axes[0][0].set_ylim(0, HEADROOM * highest)
    axes[0][0].set_ylabel("orders delivered per hour", fontsize=9.5, color=INK_SECONDARY)
    axes[1][0].set_ylabel(f"gridlocked runs (of {runs})", fontsize=9.5, color=INK_SECONDARY)
    figure.legend(
        loc="upper left",
        bbox_to_anchor=(0.055, 1.0),
        ncols=2,
        frameon=False,
        fontsize=9.5,
        labelcolor=INK,
        handlelength=1.6,
    )
    figure.text(
        0.99,
        0.012,
        f"Line: mean of {runs} seeded one-hour runs. Band: minimum to maximum.",
        ha="right",
        fontsize=8.5,
        color=INK_SECONDARY,
    )
    figure.tight_layout(rect=(0, 0.03, 1, 0.94))
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(figure)


def plot_oneshot(summary: Sequence[dict], path: Path) -> None:
    """Instances solved by CBS and by prioritised planning versus the number of agents."""
    layouts = list(dict.fromkeys(r["layout"] for r in summary))
    figure, axes = plt.subplots(
        1, len(layouts), figsize=(8.2, 3.5), sharey=True, facecolor=SURFACE, squeeze=False
    )
    instances = int(summary[0]["instances"])
    solvers = (
        ("prioritised_solved", "reservation", "Prioritised planning (index order)"),
        ("cbs_solved", "cbs", "Conflict-Based Search (2,000-node budget)"),
    )
    for column, layout in enumerate(layouts):
        axis = axes[0][column]
        rows = [r for r in summary if r["layout"] == layout]
        agents = [int(r["agents"]) for r in rows]
        for key, series, label in solvers:
            solved = [int(r[key]) for r in rows]
            _line(axis, agents, solved, SERIES_COLOUR[series], label if column == 0 else None)
            axis.annotate(
                f"{solved[-1]}",
                (agents[-1], solved[-1]),
                xytext=(7, 0),
                textcoords="offset points",
                va="center",
                fontsize=9,
                color=INK,
            )
        axis.set_title(LAYOUT_TITLE[layout], fontsize=11, color=INK, loc="left", pad=8)
        axis.set_xlabel("agents in the instance", fontsize=9.5, color=INK_SECONDARY)
        axis.set_xticks(agents)
        axis.set_xlim(agents[0] - 0.6, agents[-1] + 1.4)
        axis.set_ylim(-1, instances + 1)
        axis.set_yticks(range(0, instances + 1, 5))
        _style(axis)
    axes[0][0].set_ylabel(f"instances solved (of {instances})", fontsize=9.5, color=INK_SECONDARY)
    figure.legend(
        loc="upper left",
        bbox_to_anchor=(0.07, 1.0),
        ncols=2,
        frameon=False,
        fontsize=9.5,
        labelcolor=INK,
        handlelength=1.6,
    )
    figure.tight_layout(rect=(0, 0, 1, 0.9))
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(figure)
