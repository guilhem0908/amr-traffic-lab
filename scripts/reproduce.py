"""Regenerate every result table and figure of this repository.

    python scripts/reproduce.py                  # everything (runtimes are in the README)
    python scripts/reproduce.py --only figures   # redraw docs/ from the committed tables
    python scripts/reproduce.py --smoke          # rerun a subset and compare with results/

All seeds are fixed. The tables under results/ are identical on every platform
except results/timings.json, which holds wall-clock measurements (stage "latency").
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import sys
import time
from pathlib import Path

from amr_traffic_lab import experiments as ex
from amr_traffic_lab.layout import LAYOUT_NAMES, load_layout
from amr_traffic_lab.managers import MANAGERS
from amr_traffic_lab.sim import FleetSim

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
DOCS = ROOT / "docs"

STAGES = ("lifelong", "oneshot", "crosscheck", "latency", "figures")

SMOKE_FLEETS = (2, 8)
SMOKE_SEEDS = (0, 1)
SMOKE_ONESHOT_AGENTS = (2, 4)
SMOKE_ONESHOT_INSTANCES = 5


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


def without_seconds(row: dict) -> dict:
    return {key: value for key, value in row.items() if not key.startswith("seconds")}


def update_timings(**sections) -> None:
    """Merge ``sections`` into results/timings.json, the only machine-dependent result file."""
    path = RESULTS / "timings.json"
    timings = json.loads(path.read_text("utf-8")) if path.exists() else {}
    timings.update(machine=machine(), **sections)
    write_json(path, timings)


def record_stage(name: str, seconds: float, workers: int) -> None:
    path = RESULTS / "timings.json"
    stages = json.loads(path.read_text("utf-8")).get("stages", {}) if path.exists() else {}
    stages[name] = {"seconds": round(seconds), "workers": 1 if name == "latency" else workers}
    update_timings(stages=stages)


def machine() -> dict:
    return {
        "cpu": cpu_name(),
        "logical_cores": os.cpu_count(),
        "os": platform.system(),
        "python": platform.python_version(),
    }


def cpu_name() -> str:
    """Commercial name of the processor, falling back to what ``platform`` reports."""
    try:
        if platform.system() == "Windows":
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            )
            return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        for line in Path("/proc/cpuinfo").read_text("utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def stage_lifelong(workers: int) -> None:
    jobs = ex.lifelong_jobs()
    print(f"lifelong: {len(jobs)} one-hour runs on {workers} workers")
    rows = ex.run_parallel(ex.run_lifelong_job, jobs, workers)
    write_csv(RESULTS / "lifelong_runs.csv", rows)
    write_csv(RESULTS / "lifelong_summary.csv", ex.summarise_lifelong(rows))


def stage_oneshot(workers: int) -> None:
    jobs = ex.oneshot_jobs()
    print(f"oneshot: {len(jobs)} instances, CBS and prioritised planning")
    rows = [without_seconds(row) for row in ex.run_parallel(ex.run_oneshot_job, jobs, workers)]
    write_csv(RESULTS / "oneshot_runs.csv", rows)
    write_csv(RESULTS / "oneshot_summary.csv", ex.summarise_oneshot(rows))


def stage_crosscheck(workers: int) -> None:
    print(f"crosscheck: CBS against exhaustive search on {ex.CROSSCHECK_INSTANCES} instances")
    report = ex.crosscheck(workers=workers)
    write_json(RESULTS / "crosscheck.json", report)


def stage_latency(workers: int) -> None:
    """Wall-clock numbers are measured on one core, whatever ``workers`` says."""
    print("latency: planning time per trip and one-shot solver runtime, on one core")
    update_timings(planning_latency=ex.planning_latency(), oneshot=ex.oneshot_seconds())


def stage_figures(workers: int) -> None:
    from amr_traffic_lab import plots, render

    print("figures: docs/throughput.png, docs/oneshot.png, docs/layouts.png, docs/hero.gif")
    plots.plot_lifelong(read_csv(RESULTS / "lifelong_summary.csv"), DOCS / "throughput.png")
    plots.plot_oneshot(read_csv(RESULTS / "oneshot_summary.csv"), DOCS / "oneshot.png")
    render.render_layouts([load_layout(name) for name in LAYOUT_NAMES], DOCS / "layouts.png")

    layout = load_layout(ex.HERO_LAYOUT)
    captions = {
        "naive": ("Naive manager", "independent A* routes + wait"),
        "reservation": ("Reservation manager", "space-time A* + reservation table"),
    }
    accents = {"naive": "#d95926", "reservation": "#3987e5"}
    panels = []
    for name in ("naive", "reservation"):
        sim = FleetSim(layout, MANAGERS[name](), ex.HERO_ROBOTS, ex.HERO_SEED, record_frames=True)
        result = sim.run(ex.HERO_SECONDS)
        panels.append(
            render.Panel(*captions[name], accents[name], sim.frames, result.gridlock_tick)
        )
    width, height, frames = render.render_side_by_side(
        layout, panels, ex.HERO_SECONDS, DOCS / "hero.gif"
    )
    size_mb = (DOCS / "hero.gif").stat().st_size / 1e6
    print(f"  hero.gif: {width}x{height}, {frames} frames, {size_mb:.2f} MB")


def smoke(workers: int) -> int:
    """Rerun a subset of every deterministic experiment and compare with the committed rows."""
    failures = 0

    committed = {
        (r["layout"], r["manager"], r["robots"], r["seed"]): r
        for r in read_csv(RESULTS / "lifelong_runs.csv")
    }
    jobs = ex.lifelong_jobs(fleets=SMOKE_FLEETS, seeds=SMOKE_SEEDS)
    for row in ex.run_parallel(ex.run_lifelong_job, jobs, workers):
        fresh = {key: str(value) for key, value in row.items()}
        key = (fresh["layout"], fresh["manager"], fresh["robots"], fresh["seed"])
        if committed.get(key) != fresh:
            failures += 1
            print(
                f"MISMATCH lifelong {key}:\n  committed {committed.get(key)}\n  fresh     {fresh}"
            )
    print(f"lifelong: {len(jobs)} runs compared")

    committed = {
        (r["layout"], r["agents"], r["instance"]): r for r in read_csv(RESULTS / "oneshot_runs.csv")
    }
    jobs = [
        ex.OneshotJob(layout, agents, instance)
        for layout in ex.ONESHOT_LAYOUTS
        for agents in SMOKE_ONESHOT_AGENTS
        for instance in range(SMOKE_ONESHOT_INSTANCES)
    ]
    for row in ex.run_parallel(ex.run_oneshot_job, jobs, workers):
        fresh = {key: str(value) for key, value in without_seconds(row).items()}
        key = (fresh["layout"], fresh["agents"], fresh["instance"])
        if committed.get(key) != fresh:
            failures += 1
            print(f"MISMATCH oneshot {key}:\n  committed {committed.get(key)}\n  fresh     {fresh}")
    print(f"oneshot: {len(jobs)} instances compared")

    report = ex.crosscheck(workers=workers)
    if report != json.loads((RESULTS / "crosscheck.json").read_text("utf-8")):
        failures += 1
        print(f"MISMATCH crosscheck: fresh {report}")
    print(f"crosscheck: {report['instances']} instances compared")

    print("smoke check passed" if not failures else f"smoke check FAILED ({failures} mismatches)")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=STAGES, action="append", help="run only these stages")
    parser.add_argument("--smoke", action="store_true", help="compare a rerun subset with results/")
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()
    if args.smoke:
        return smoke(args.workers)
    stages = {
        "lifelong": stage_lifelong,
        "oneshot": stage_oneshot,
        "crosscheck": stage_crosscheck,
        "latency": stage_latency,
        "figures": stage_figures,
    }
    for name in args.only or STAGES:
        started = time.perf_counter()
        stages[name](args.workers)
        record_stage(name, time.perf_counter() - started, args.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
