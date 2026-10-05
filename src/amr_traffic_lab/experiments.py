"""Seeded experiments: every number quoted in the README comes from here.

All functions are deterministic for a given seed, on any platform, except the
wall-clock fields, which are kept in separate keys (prefixed ``seconds``) and
written to a separate file.
"""

from __future__ import annotations

import multiprocessing
import random
import statistics
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from .cbs import SOLVED, solve_cbs, solve_prioritised
from .exhaustive import optimal_sum_of_costs
from .layout import LAYOUT_NAMES, Layout, load_layout
from .managers import MANAGERS, ReservationManager
from .safety import validate_paths
from .sim import NO_GRIDLOCK, SECONDS_PER_TICK, TICKS_PER_HOUR, FleetSim

FLEET_SIZES = (1, 2, 3, 4, 6, 8, 10, 12, 14, 16)
LIFELONG_SEEDS = tuple(range(20))
SATURATION_SHARE = 0.95
"""The saturation fleet is the smallest one that reaches this share of the best throughput."""

ONESHOT_LAYOUTS = ("open_floor", "narrow_aisles")
ONESHOT_AGENTS = (2, 4, 6, 8, 10, 12)
ONESHOT_INSTANCES = 25
ONESHOT_NODE_BUDGET = 2000

CROSSCHECK_INSTANCES = 400
CROSSCHECK_NODE_BUDGET = 3000
CROSSCHECK_MAX_SIDE = 4

LATENCY_LAYOUT = "single_corridor"
LATENCY_FLEETS = (2, 4, 8, 12, 16)
LATENCY_SEEDS = (0, 1, 2)

HERO_LAYOUT = "single_corridor"
HERO_ROBOTS = 12
HERO_SEED = 0
HERO_SECONDS = 200
"""The clip of docs/hero.gif: first seed, first 200 s, no selection."""


@dataclass(frozen=True)
class LifelongJob:
    layout: str
    manager: str
    robots: int
    seed: int
    ticks: int = TICKS_PER_HOUR


@dataclass(frozen=True)
class OneshotJob:
    layout: str
    agents: int
    instance: int


def run_parallel(function: Callable, jobs: Sequence, workers: int) -> list:
    """Map ``function`` over ``jobs``; results keep the order of ``jobs``."""
    if workers <= 1:
        return [function(job) for job in jobs]
    with multiprocessing.Pool(workers) as pool:
        return pool.map(function, jobs, chunksize=1)


# --------------------------------------------------------------------------- lifelong


def lifelong_jobs(
    layouts: Iterable[str] = LAYOUT_NAMES,
    fleets: Iterable[int] = FLEET_SIZES,
    seeds: Iterable[int] = LIFELONG_SEEDS,
    ticks: int = TICKS_PER_HOUR,
) -> list[LifelongJob]:
    return [
        LifelongJob(layout, manager, robots, seed, ticks)
        for layout in layouts
        for manager in sorted(MANAGERS)
        for robots in fleets
        for seed in seeds
    ]


def run_lifelong_job(job: LifelongJob) -> dict:
    """One simulated shift; the returned row has no wall-clock field."""
    sim = FleetSim(load_layout(job.layout), MANAGERS[job.manager](), job.robots, job.seed)
    return sim.run(job.ticks).as_row()


def summarise_lifelong(rows: Iterable[dict]) -> list[dict]:
    """Aggregate runs per (layout, manager, fleet size).

    Throughput is in orders per hour of simulated time; a gridlocked run keeps
    its count for the whole shift, since nothing is delivered after the jam.
    The mean order time pools all delivered orders of the group.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["layout"], row["manager"], int(row["robots"]))].append(row)
    summary = []
    for (layout, manager, robots), runs in groups.items():
        per_hour = [int(run["delivered"]) * TICKS_PER_HOUR / int(run["ticks"]) for run in runs]
        delivered = sum(int(run["delivered"]) for run in runs)
        order_ticks = sum(int(run["order_ticks"]) for run in runs)
        travel = sum(int(run["travel_ticks"]) for run in runs)
        jams = [int(run["gridlock_tick"]) for run in runs if int(run["gridlock_tick"]) >= 0]
        summary.append(
            {
                "layout": layout,
                "manager": manager,
                "robots": robots,
                "runs": len(runs),
                "delivered": delivered,
                "throughput_mean": round(statistics.fmean(per_hour), 1),
                "throughput_min": round(min(per_hour), 1),
                "throughput_max": round(max(per_hour), 1),
                "gridlocked_runs": len(jams),
                "median_gridlock_s": (
                    round(statistics.median(jams) * SECONDS_PER_TICK) if jams else NO_GRIDLOCK
                ),
                "order_time_mean_s": (
                    round(order_ticks * SECONDS_PER_TICK / delivered, 1) if delivered else 0.0
                ),
                "wait_share": (
                    round(sum(int(run["wait_ticks"]) for run in runs) / travel, 3)
                    if travel
                    else 0.0
                ),
                "violations": sum(int(run["violations"]) for run in runs),
                "safety_checks": sum(int(run["safety_checks"]) for run in runs),
            }
        )
    summary.sort(key=lambda r: (LAYOUT_NAMES.index(r["layout"]), r["manager"], r["robots"]))
    return summary


def best_fleet(summary: Iterable[dict], layout: str, manager: str) -> dict:
    """Summary row with the highest mean throughput (smallest fleet on ties)."""
    rows = [r for r in summary if r["layout"] == layout and r["manager"] == manager]
    return max(rows, key=lambda r: (float(r["throughput_mean"]), -int(r["robots"])))


def saturation_fleet(summary: Iterable[dict], layout: str, manager: str) -> dict:
    """Smallest fleet whose mean throughput reaches ``SATURATION_SHARE`` of the best one."""
    rows = [r for r in summary if r["layout"] == layout and r["manager"] == manager]
    best = max(float(r["throughput_mean"]) for r in rows)
    reaching = [r for r in rows if float(r["throughput_mean"]) >= SATURATION_SHARE * best]
    return min(reaching, key=lambda r: int(r["robots"]))


# --------------------------------------------------------------------------- one-shot


def oneshot_jobs() -> list[OneshotJob]:
    return [
        OneshotJob(layout, agents, instance)
        for layout in ONESHOT_LAYOUTS
        for agents in ONESHOT_AGENTS
        for instance in range(ONESHOT_INSTANCES)
    ]


def oneshot_instance(layout: Layout, agents: int, seed: int) -> tuple[list[int], list[int]]:
    """Distinct random starts and distinct random goals on aisle cells."""
    rng = random.Random(seed)
    aisle_cells = [cell for cell in layout.free if layout.aisle[cell]]
    return rng.sample(aisle_cells, agents), rng.sample(aisle_cells, agents)


def run_oneshot_job(job: OneshotJob) -> dict:
    """Solve one instance with CBS and with prioritised planning."""
    layout = load_layout(job.layout)
    seed = 1000 * job.agents + job.instance
    starts, goals = oneshot_instance(layout, job.agents, seed)

    started = time.perf_counter()
    cbs = solve_cbs(layout, starts, goals, ONESHOT_NODE_BUDGET)
    cbs_seconds = time.perf_counter() - started
    started = time.perf_counter()
    prioritised = solve_prioritised(layout, starts, goals)
    prioritised_seconds = time.perf_counter() - started

    invalid = 0
    for result in (cbs, prioritised):
        if result.status == SOLVED and validate_paths(layout, starts, goals, result.paths):
            invalid += 1
    return {
        "layout": job.layout,
        "agents": job.agents,
        "instance": job.instance,
        "lower_bound": sum(layout.distances_to(g)[s] for s, g in zip(starts, goals, strict=True)),
        "cbs_status": cbs.status,
        "cbs_cost": cbs.cost if cbs.cost is not None else "",
        "cbs_nodes": cbs.high_level_nodes,
        "cbs_expansions": cbs.low_level_expansions,
        "prioritised_status": prioritised.status,
        "prioritised_cost": prioritised.cost if prioritised.cost is not None else "",
        "prioritised_expansions": prioritised.low_level_expansions,
        "invalid_plans": invalid,
        "seconds_cbs": cbs_seconds,
        "seconds_prioritised": prioritised_seconds,
    }


def _oneshot_order(item: tuple) -> tuple[int, int]:
    (layout, agents), _ = item
    return ONESHOT_LAYOUTS.index(layout), agents


def summarise_oneshot(rows: Iterable[dict]) -> list[dict]:
    """Per (layout, agents): success counts, CBS effort and the cost overhead of prioritised
    planning on the instances both solvers finish."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["layout"], int(row["agents"]))].append(row)
    summary = []
    for (layout, agents), runs in sorted(groups.items(), key=_oneshot_order):
        cbs_solved = [r for r in runs if r["cbs_status"] == SOLVED]
        both = [r for r in cbs_solved if r["prioritised_status"] == SOLVED]
        cbs_cost = sum(int(r["cbs_cost"]) for r in both)
        prioritised_cost = sum(int(r["prioritised_cost"]) for r in both)
        summary.append(
            {
                "layout": layout,
                "agents": agents,
                "instances": len(runs),
                "cbs_solved": len(cbs_solved),
                "prioritised_solved": sum(r["prioritised_status"] == SOLVED for r in runs),
                "both_solved": len(both),
                "cbs_median_nodes": (
                    int(statistics.median(int(r["cbs_nodes"]) for r in cbs_solved))
                    if cbs_solved
                    else ""
                ),
                "prioritised_overhead_pct": (
                    round(100 * (prioritised_cost - cbs_cost) / cbs_cost, 2) if both else ""
                ),
                "invalid_plans": sum(int(r["invalid_plans"]) for r in runs),
            }
        )
    return summary


def oneshot_seconds() -> list[dict]:
    """Median wall-clock of both solvers per (layout, agents), measured serially.

    CBS runs that hit the node budget are included, so the CBS median stops
    growing once most instances run out of budget.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for job in oneshot_jobs():
        groups[(job.layout, job.agents)].append(run_oneshot_job(job))
    return [
        {
            "layout": layout,
            "agents": agents,
            "cbs_median_ms": round(1000 * statistics.median(r["seconds_cbs"] for r in runs), 2),
            "cbs_max_ms": round(1000 * max(r["seconds_cbs"] for r in runs), 1),
            "prioritised_median_ms": round(
                1000 * statistics.median(r["seconds_prioritised"] for r in runs), 2
            ),
        }
        for (layout, agents), runs in sorted(groups.items(), key=_oneshot_order)
    ]


# --------------------------------------------------------------------------- cross-check


def tiny_instance(seed: int) -> tuple[Layout, list[int], list[int]]:
    """Two or three agents on a random grid of at most 4 x 4 cells with 15 % walls."""
    rng = random.Random(seed)
    rows, cols = (
        rng.randrange(2, CROSSCHECK_MAX_SIDE + 1),
        rng.randrange(2, CROSSCHECK_MAX_SIDE + 1),
    )
    lines = ["".join("#" if rng.random() < 0.15 else "." for _ in range(cols)) for _ in range(rows)]
    if all("." not in line for line in lines):
        lines[0] = "." + lines[0][1:]
    layout = Layout("tiny", lines)
    agents = min(rng.randrange(2, 4), len(layout.free))
    return layout, rng.sample(layout.free, agents), rng.sample(layout.free, agents)


def crosscheck_one(seed: int) -> str:
    """Compare CBS with the exhaustive joint-state search on one tiny instance."""
    layout, starts, goals = tiny_instance(seed)
    optimum = optimal_sum_of_costs(layout, starts, goals)
    result = solve_cbs(layout, starts, goals, CROSSCHECK_NODE_BUDGET)
    if optimum is None:
        return "unsolvable_cbs_claims_a_plan" if result.status == SOLVED else "unsolvable"
    if result.status != SOLVED:
        return "budget"
    valid = not validate_paths(layout, starts, goals, result.paths)
    if not valid or result.cost != optimum:
        return "mismatch"
    return "optimal_after_split" if result.high_level_nodes > 1 else "optimal_at_root"


def crosscheck(instances: int = CROSSCHECK_INSTANCES, workers: int = 1) -> dict:
    outcomes = run_parallel(crosscheck_one, range(instances), workers)
    counts = {name: outcomes.count(name) for name in sorted(set(outcomes))}
    solvable = (
        instances - counts.get("unsolvable", 0) - counts.get("unsolvable_cbs_claims_a_plan", 0)
    )
    optimal = counts.get("optimal_after_split", 0) + counts.get("optimal_at_root", 0)
    return {
        "instances": instances,
        "solvable": solvable,
        "cbs_finished": optimal + counts.get("mismatch", 0),
        "cbs_equal_to_optimum": optimal,
        "cbs_needed_a_split": counts.get("optimal_after_split", 0),
        "cbs_out_of_budget": counts.get("budget", 0),
        "mismatches": counts.get("mismatch", 0) + counts.get("unsolvable_cbs_claims_a_plan", 0),
        "node_budget": CROSSCHECK_NODE_BUDGET,
    }


# --------------------------------------------------------------------------- latency


def planning_latency() -> list[dict]:
    """Wall-clock of one trip plan of the reservation manager, per fleet size (run serially)."""
    layout = load_layout(LATENCY_LAYOUT)
    rows = []
    for robots in LATENCY_FLEETS:
        seconds: list[float] = []
        for seed in LATENCY_SEEDS:
            manager = ReservationManager()
            FleetSim(layout, manager, robots, seed).run(TICKS_PER_HOUR)
            seconds.extend(manager.plan_seconds)
        seconds.sort()
        rows.append(
            {
                "layout": LATENCY_LAYOUT,
                "robots": robots,
                "plans": len(seconds),
                "median_ms": round(1000 * statistics.median(seconds), 3),
                "p95_ms": round(1000 * seconds[int(0.95 * (len(seconds) - 1))], 3),
                "max_ms": round(1000 * seconds[-1], 2),
            }
        )
    return rows
