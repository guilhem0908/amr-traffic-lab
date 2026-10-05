"""Command line: run one simulated shift and print what happened."""

from __future__ import annotations

import argparse

from .layout import LAYOUT_NAMES, load_layout
from .managers import MANAGERS
from .sim import NO_GRIDLOCK, SECONDS_PER_TICK, TICKS_PER_HOUR, FleetSim


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="amr-traffic-lab", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    simulate = commands.add_parser("simulate", help="simulate one fleet on one layout")
    simulate.add_argument("--layout", choices=LAYOUT_NAMES, default="single_corridor")
    simulate.add_argument("--manager", choices=sorted(MANAGERS), default="reservation")
    simulate.add_argument("--robots", type=int, default=12)
    simulate.add_argument("--seed", type=int, default=0)
    simulate.add_argument("--seconds", type=int, default=TICKS_PER_HOUR, help="simulated time")
    commands.add_parser("layouts", help="print the shipped floor plans")
    return parser


def simulate(layout_name: str, manager_name: str, robots: int, seed: int, seconds: int) -> str:
    ticks = int(seconds / SECONDS_PER_TICK)
    sim = FleetSim(load_layout(layout_name), MANAGERS[manager_name](), robots, seed)
    result = sim.run(ticks)
    per_hour = result.delivered * TICKS_PER_HOUR / ticks
    order_time = result.order_ticks * SECONDS_PER_TICK / result.delivered if result.delivered else 0
    waiting = 100 * result.wait_ticks / result.travel_ticks if result.travel_ticks else 0
    if result.gridlock_tick == NO_GRIDLOCK:
        gridlock = "none"
    else:
        gridlock = f"no robot has moved since t = {result.gridlock_tick} s"
    return "\n".join(
        [
            f"{layout_name}, {manager_name} manager, {robots} robots, seed {seed}, {seconds} s",
            f"  orders delivered  {result.delivered} ({per_hour:.1f} per hour)",
            f"  mean order time   {order_time:.1f} s from assignment to delivery",
            f"  waiting           {waiting:.1f} % of the time spent on trips",
            f"  gridlock          {gridlock}",
            f"  safety check      {result.safety_checks} ticks, {result.violations} violations",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "layouts":
        for name in LAYOUT_NAMES:
            print(f"{name}\n{load_layout(name).to_text()}\n")
    else:
        print(simulate(args.layout, args.manager, args.robots, args.seed, args.seconds))
    return 0
