"""Keep the numbers of README.md equal to the committed results.

    python scripts/check_readme.py           # exit 1 if a generated block is out of date
    python scripts/check_readme.py --write   # rewrite the generated blocks in place

Every table and every sentence that quotes a result lives between
``<!-- BEGIN GENERATED: name -->`` and ``<!-- END GENERATED: name -->`` markers
and is rebuilt here from results/*.csv and results/*.json.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import struct
import sys
from pathlib import Path

from amr_traffic_lab.experiments import (
    HERO_LAYOUT,
    HERO_ROBOTS,
    HERO_SECONDS,
    HERO_SEED,
    SATURATION_SHARE,
    best_fleet,
    saturation_fleet,
)
from amr_traffic_lab.layout import LAYOUT_NAMES

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
README = ROOT / "README.md"
HERO = ROOT / "docs" / "hero.gif"

TABLE_FLEETS = (2, 4, 8, 16)
TABLE_AGENTS = (4, 8, 12)
LARGEST_FLEET = 16
HERO_MAX_BYTES = 3_000_000
HERO_MAX_WIDTH = 900
LAYOUT_TITLE = {
    "open_floor": "Open floor",
    "narrow_aisles": "Narrow aisles",
    "single_corridor": "Single corridor",
}
NO_VALUE = "-"


def read_csv(name: str) -> list[dict]:
    with (RESULTS / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_json(name: str) -> dict:
    return json.loads((RESULTS / name).read_text("utf-8"))


def share(count: int, total: int) -> str:
    """Raw count next to its percentage, e.g. ``12 / 20 (60%)``."""
    return f"{count} / {total} ({100 * count / total:.0f}%)"


def fleet(count: int | str) -> str:
    return f"{count} robot" if int(count) == 1 else f"{count} robots"


def milliseconds(value: float) -> str:
    """Two significant decimals below 10 ms, one below 100 ms, none above."""
    if value < 10:
        return f"{value:.2f} ms"
    return f"{value:.1f} ms" if value < 100 else f"{value:.0f} ms"


def cpu_label(timings: dict) -> str:
    return timings["machine"]["cpu"].replace("(R)", "").replace("(TM)", "").strip()


def pick(summary: list[dict], layout: str, manager: str, robots: int) -> dict:
    (row,) = (
        r
        for r in summary
        if r["layout"] == layout and r["manager"] == manager and int(r["robots"]) == robots
    )
    return row


def gridlocks(summary: list[dict], layout: str | None, manager: str, min_robots: int = 1):
    rows = [
        r
        for r in summary
        if r["manager"] == manager
        and (layout is None or r["layout"] == layout)
        and int(r["robots"]) >= min_robots
    ]
    return sum(int(r["gridlocked_runs"]) for r in rows), sum(int(r["runs"]) for r in rows)


def hero_block(summary: list[dict], runs: list[dict]) -> str:
    row = pick(summary, HERO_LAYOUT, "naive", HERO_ROBOTS)
    (clip,) = (
        r
        for r in runs
        if (r["layout"], r["manager"]) == (HERO_LAYOUT, "naive")
        and (int(r["robots"]), int(r["seed"])) == (HERO_ROBOTS, HERO_SEED)
    )
    return (
        f"*Same layout, same {HERO_ROBOTS} robots, same seeded orders: seed {HERO_SEED}, first "
        f"{HERO_SECONDS} s, drawn by `scripts/reproduce.py`. The clip is not hand-picked: with "
        f"{HERO_ROBOTS} robots the naive manager gridlocks on this layout in "
        f"{row['gridlocked_runs']} of {row['runs']} seeds. Here nothing moves on the left after "
        f"t = {clip['gridlock_tick']} s.*"
    )


def verdict_block(summary: list[dict]) -> str:
    reserved = gridlocks(summary, None, "reservation")
    corridor = gridlocks(summary, "single_corridor", "naive", min_robots=2)
    aisles = gridlocks(summary, "narrow_aisles", "naive")
    floor = gridlocks(summary, "open_floor", "naive")
    knee = saturation_fleet(summary, "single_corridor", "reservation")
    largest = pick(summary, "single_corridor", "reservation", LARGEST_FLEET)
    seeds = summary[0]["runs"]
    return (
        f"**In the single corridor the naive manager gridlocked in {corridor[0]} of "
        f"{corridor[1]} one-hour runs with two robots or more, while the reservation manager "
        f"never gridlocked ({reserved[0]} of {reserved[1]} runs over the three layouts) and its "
        f"throughput levelled off at {fleet(knee['robots'])} ({knee['throughput_mean']} "
        f"orders/h, against {largest['throughput_mean']} with {LARGEST_FLEET}).** The naive "
        f"manager is not a strawman: it gridlocked in {floor[0]} of {floor[1]} runs on the open "
        f"floor and in {aisles[0]} of {aisles[1]} in the narrow aisles. Each cell below is "
        f"{seeds} seeded runs of one simulated hour; the full grid of ten fleet sizes is in "
        f"`results/lifelong_summary.csv`."
    )


def lifelong_block(summary: list[dict]) -> str:
    lines = [
        "| Layout | Robots | Reservation, orders/h (min-max) | Naive, orders/h (min-max) "
        "| Reservation gridlocks | Naive gridlocks | Reservation order time | Naive order time |",
        "|:--|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for layout in LAYOUT_NAMES:
        for robots in TABLE_FLEETS:
            cells = [LAYOUT_TITLE[layout], str(robots)]
            rows = [pick(summary, layout, m, robots) for m in ("reservation", "naive")]
            for row in rows:
                low, high = float(row["throughput_min"]), float(row["throughput_max"])
                cells.append(f"{row['throughput_mean']} ({low:.0f}-{high:.0f})")
            cells += [share(int(row["gridlocked_runs"]), int(row["runs"])) for row in rows]
            for row in rows:
                delivered = int(row["delivered"])
                cells.append(f"{row['order_time_mean_s']} s" if delivered else NO_VALUE)
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def saturation_block(summary: list[dict]) -> str:
    percent = round(100 * SATURATION_SHARE)
    lines = [
        f"| Layout | Reservation, best orders/h | {percent}% of best reached with "
        f"| Trip time spent waiting at {LARGEST_FLEET} robots | Naive, best orders/h "
        "| Smallest naive fleet with a gridlock |",
        "|:--|--:|--:|--:|--:|--:|",
    ]
    for layout in LAYOUT_NAMES:
        best = best_fleet(summary, layout, "reservation")
        knee = saturation_fleet(summary, layout, "reservation")
        waiting = 100 * float(pick(summary, layout, "reservation", LARGEST_FLEET)["wait_share"])
        naive_best = best_fleet(summary, layout, "naive")
        jammed = [
            int(r["robots"])
            for r in summary
            if r["layout"] == layout and r["manager"] == "naive" and int(r["gridlocked_runs"])
        ]
        lines.append(
            f"| {LAYOUT_TITLE[layout]} "
            f"| {best['throughput_mean']} ({fleet(best['robots'])}) "
            f"| {fleet(knee['robots'])} ({knee['throughput_mean']}) "
            f"| {waiting:.1f}% "
            f"| {naive_best['throughput_mean']} ({fleet(naive_best['robots'])}) "
            f"| {fleet(min(jammed))} |"
        )
    return "\n".join(lines)


def oneshot_block(summary: list[dict], seconds: list[dict]) -> str:
    lines = [
        "| Layout | Agents | CBS solved | Prioritised solved | Extra cost of prioritised "
        "| CBS median time | Prioritised median time |",
        "|:--|--:|--:|--:|--:|--:|--:|",
    ]
    timing = {(r["layout"], int(r["agents"])): r for r in seconds}
    for row in summary:
        agents = int(row["agents"])
        if agents not in TABLE_AGENTS:
            continue
        total = int(row["instances"])
        if int(row["both_solved"]):
            extra = float(row["prioritised_overhead_pct"])
            overhead = f"+{extra:.1f}% on {row['both_solved']} instances"
        else:
            overhead = NO_VALUE
        clock = timing[(row["layout"], agents)]
        lines.append(
            f"| {LAYOUT_TITLE[row['layout']]} | {agents} "
            f"| {share(int(row['cbs_solved']), total)} "
            f"| {share(int(row['prioritised_solved']), total)} "
            f"| {overhead} "
            f"| {milliseconds(clock['cbs_median_ms'])} "
            f"| {milliseconds(clock['prioritised_median_ms'])} |"
        )
    return "\n".join(lines)


def checks_block(runs: list[dict], oneshot: list[dict], cross: dict, timings: dict) -> str:
    ticks = sum(int(r["safety_checks"]) for r in runs)
    violations = sum(int(r["violations"]) for r in runs)
    invalid = sum(int(r["invalid_plans"]) for r in oneshot)
    plans = sum(
        (r["cbs_status"] == "solved") + (r["prioritised_status"] == "solved") for r in oneshot
    )
    smallest, latency = timings["planning_latency"][0], timings["planning_latency"][-1]
    return "\n".join(
        [
            f"- **Safety invariant:** {violations} vertex or swap conflicts in {ticks:,} simulated "
            f"ticks ({len(runs):,} runs, both managers), and {invalid} invalid plans among the "
            f"{plans} one-shot plans returned. The check shares no code with the planners.",
            f"- **CBS optimality:** on {cross['instances']} random tiny instances, "
            f"{cross['solvable']} are solvable; CBS finished {cross['cbs_finished']} of them "
            f"within {cross['node_budget']:,} nodes and its cost equals the exhaustive "
            f"joint-state optimum on {cross['cbs_equal_to_optimum']} of {cross['cbs_finished']} "
            f"({cross['cbs_needed_a_split']} needed at least one constraint split). The other "
            f"{cross['cbs_out_of_budget']} ran out of budget.",
            f"- **Planning time:** in the single corridor one trip of the reservation manager "
            f"takes {milliseconds(smallest['median_ms'])} (median) with "
            f"{fleet(smallest['robots'])} and {milliseconds(latency['median_ms'])} with "
            f"{latency['robots']} (95th percentile {milliseconds(latency['p95_ms'])} over "
            f"{latency['plans']:,} plans), in pure Python on one core of an "
            f"{cpu_label(timings)}. Every fleet size is in `results/timings.json`.",
        ]
    )


def runtime_block(timings: dict, runs: list[dict], oneshot: list[dict]) -> str:
    stages = timings["stages"]
    return (
        f"Runtime of `python scripts/reproduce.py`, measured on an {cpu_label(timings)} "
        f"(CPU only; nothing in this repository uses a GPU): "
        f"{stages['lifelong']['seconds']} s for the {len(runs):,} lifelong runs on "
        f"{stages['lifelong']['workers']} worker processes, "
        f"{stages['oneshot']['seconds']} s for the {len(oneshot)} one-shot instances, "
        f"{stages['crosscheck']['seconds']} s for the CBS cross-check, "
        f"{stages['latency']['seconds']} s for the wall-clock measurements on one core and "
        f"{stages['figures']['seconds']} s for the figures and the GIF. On another machine "
        f"only `results/timings.json` changes; `python scripts/check_readme.py --write` then "
        f"refreshes the sentences that quote it."
    )


def expected_blocks() -> dict[str, str]:
    summary = read_csv("lifelong_summary.csv")
    oneshot_summary = read_csv("oneshot_summary.csv")
    timings = read_json("timings.json")
    return {
        "hero": hero_block(summary, read_csv("lifelong_runs.csv")),
        "verdict": verdict_block(summary),
        "lifelong": lifelong_block(summary),
        "saturation": saturation_block(summary),
        "oneshot": oneshot_block(oneshot_summary, timings["oneshot"]),
        "checks": checks_block(
            read_csv("lifelong_runs.csv"),
            read_csv("oneshot_runs.csv"),
            read_json("crosscheck.json"),
            timings,
        ),
        "runtime": runtime_block(
            timings, read_csv("lifelong_runs.csv"), read_csv("oneshot_runs.csv")
        ),
    }


def block_pattern(name: str) -> re.Pattern:
    """Match a block; group 2 is its body, each line of it ending with a newline."""
    return re.compile(
        rf"(<!-- BEGIN GENERATED: {name} -->\n)(.*?)(<!-- END GENERATED: {name} -->)", re.DOTALL
    )


def hero_problems() -> list[str]:
    """The hero GIF must stay light enough for a README; sizes are read from its header."""
    data = HERO.read_bytes()
    width, height = struct.unpack("<HH", data[6:10])
    problems = []
    if len(data) > HERO_MAX_BYTES:
        problems.append(f"docs/hero.gif weighs {len(data)} bytes (limit {HERO_MAX_BYTES})")
    if width > HERO_MAX_WIDTH:
        problems.append(f"docs/hero.gif is {width}x{height} px (limit {HERO_MAX_WIDTH} wide)")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="rewrite the generated blocks")
    args = parser.parse_args()

    text = README.read_text("utf-8")
    problems = hero_problems()
    wrote = False
    for name, content in expected_blocks().items():
        expected = content + "\n"
        match = block_pattern(name).search(text)
        if match is None:
            problems.append(f"README.md has no generated block named '{name}'")
            continue
        if match.group(2) == expected:
            continue
        if args.write:
            text = block_pattern(name).sub(lambda m, body=expected: m[1] + body + m[3], text)
            wrote = True
        else:
            diff = difflib.unified_diff(
                match.group(2).splitlines(),
                expected.splitlines(),
                "README.md",
                "results",
                "",
                "",
                1,
            )
            problems.append(f"block '{name}' differs from the results:\n" + "\n".join(diff))
    if wrote:
        README.write_text(text, encoding="utf-8", newline="\n")
        print("README.md: generated blocks rewritten from results/")
    for problem in problems:
        print(problem)
    print("README.md is out of date" if problems else "README.md matches the committed results")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
