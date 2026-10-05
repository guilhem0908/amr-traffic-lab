"""Traffic managers: who moves where on the next tick.

Two managers share one interface so that the simulator, the dispatcher and
the order stream are identical for both:

    NaiveManager        independent A* routes, wait when the next cell is
                        taken, replan around visible robots after a while.
    ReservationManager  prioritised planning in space-time against a
                        reservation table, one trip at a time (Token Passing).
"""

from __future__ import annotations

import random
import time
from collections.abc import Sequence

from .layout import Layout
from .reservation import ReservationTable
from .search import shortest_path, spacetime_astar

PATIENCE_MIN = 2
PATIENCE_MAX = 6
"""A blocked naive robot waits a random number of ticks in this range before replanning."""

SENSE_RANGE = 4
"""Manhattan distance (cells) within which a naive robot sees other robots when it replans."""

PRUNE_PERIOD = 64
"""Ticks between two clean-ups of past reservations."""


class PlanningError(RuntimeError):
    """Raised when a trip cannot be planned (only possible on a layout that is not well-formed)."""


class TrafficManager:
    """Interface between the fleet simulator and a traffic policy."""

    name = "abstract"
    claims_safety = True
    """If true, the simulator raises as soon as a tick has a vertex or swap conflict."""

    def reset(self, layout: Layout, starts: Sequence[int], seed: int) -> None:
        self.layout = layout
        self.n_robots = len(starts)
        self.plan_calls = 0
        self.expansions = 0
        self.plan_seconds: list[float] = []

    def set_goal(self, robot: int, start: int, goal: int, now: int) -> None:
        """Send ``robot``, resting on endpoint ``start`` at time ``now``, to ``goal``."""
        raise NotImplementedError

    def step(self, now: int, positions: Sequence[int]) -> list[int]:
        """Return the cell of every robot at time ``now + 1``."""
        raise NotImplementedError


class NaiveManager(TrafficManager):
    """Independent shortest paths plus a local right-of-way rule.

    Every robot follows its own A* route, planned as if it were alone. Each
    tick a robot may advance only if the cell ahead is empty or is being
    vacated, it is not a head-on swap, and no robot with a smaller index wants
    the same cell. After ``patience`` blocked ticks it replans around the
    robots it currently sees; if no route exists it keeps waiting.

    The rule is safe (no vertex or swap conflict can pass it) but not live:
    two robots that meet head-on in a single-lane corridor wait for ever.
    """

    name = "naive"

    def reset(self, layout: Layout, starts: Sequence[int], seed: int) -> None:
        super().reset(layout, starts, seed)
        self._rng = random.Random(seed)
        self._routes: list[list[int]] = [[] for _ in starts]
        self._blocked_for = [0] * self.n_robots
        self._patience = [self._draw_patience() for _ in starts]

    def _draw_patience(self) -> int:
        return PATIENCE_MIN + self._rng.randrange(PATIENCE_MAX - PATIENCE_MIN + 1)

    def set_goal(self, robot: int, start: int, goal: int, now: int) -> None:
        self._plan_route(robot, start, goal, blocked=frozenset())

    def _plan_route(self, robot: int, start: int, goal: int, blocked: frozenset[int]) -> bool:
        """Store the route reversed, so that the next cell is popped from its end."""
        started = time.perf_counter()
        path = shortest_path(self.layout, start, goal, blocked)
        self.plan_seconds.append(time.perf_counter() - started)
        self.plan_calls += 1
        if path is None:
            return False
        self._routes[robot] = path[:0:-1]
        return True

    def step(self, now: int, positions: Sequence[int]) -> list[int]:
        for robot in range(self.n_robots):
            if self._routes[robot] and self._blocked_for[robot] >= self._patience[robot]:
                self._replan_around_visible(robot, positions)
        wanted = [
            route[-1] if route else cell
            for route, cell in zip(self._routes, positions, strict=True)
        ]
        granted = _grant_moves(positions, wanted)
        after = list(positions)
        for robot in range(self.n_robots):
            if robot in granted:
                after[robot] = self._routes[robot].pop()
                self._blocked_for[robot] = 0
            elif self._routes[robot]:
                self._blocked_for[robot] += 1
        return after

    def _replan_around_visible(self, robot: int, positions: Sequence[int]) -> None:
        row, col = self.layout.row_col(positions[robot])
        visible = set()
        for other, cell in enumerate(positions):
            if other == robot:
                continue
            other_row, other_col = self.layout.row_col(cell)
            if abs(other_row - row) + abs(other_col - col) <= SENSE_RANGE:
                visible.add(cell)
        goal = self._routes[robot][0]
        self._plan_route(robot, positions[robot], goal, frozenset(visible))
        self._blocked_for[robot] = 0
        self._patience[robot] = self._draw_patience()


def _grant_moves(positions: Sequence[int], wanted: Sequence[int]) -> set[int]:
    """Largest set of requested moves that the right-of-way rule lets through.

    Start from all robots that want to move, keep one per target cell (smallest
    index), then repeatedly withdraw every robot whose target is held by a
    robot that stays, or that would swap with its target's occupant, until
    nothing changes. What remains moves into empty or vacated cells only, so
    the joint move has no vertex and no swap conflict.
    """
    occupant = {cell: robot for robot, cell in enumerate(positions)}
    winner: dict[int, int] = {}
    for robot, target in enumerate(wanted):
        if target != positions[robot]:
            winner.setdefault(target, robot)
    moving = set(winner.values())
    changed = True
    while changed:
        changed = False
        for robot in sorted(moving):
            ahead = occupant.get(wanted[robot])
            if ahead is None:
                continue
            if ahead not in moving or wanted[ahead] == positions[robot]:
                moving.discard(robot)
                changed = True
    return moving


class ReservationManager(TrafficManager):
    """Prioritised space-time planning, one trip at a time (Token Passing, Ma et al. 2017).

    Invariant: every robot either rests on an endpoint that the table marks
    as parked, or follows a reserved path that ends on such an endpoint. A
    robot plans only while resting, with space-time A* against everything
    already reserved, and its new path is committed before anyone else plans.
    Paths are executed exactly as reserved, so the fleet is collision-free by
    construction; the simulator still checks it on every tick.
    """

    name = "reservation"

    def reset(self, layout: Layout, starts: Sequence[int], seed: int) -> None:
        super().reset(layout, starts, seed)
        self.table = ReservationTable()
        self._paths: list[list[int]] = [[cell] for cell in starts]
        self._path_start = [0] * self.n_robots
        for cell in starts:
            self.table.park(cell, 0)

    def _cell_at(self, robot: int, when: int) -> int:
        path = self._paths[robot]
        return path[min(when - self._path_start[robot], len(path) - 1)]

    def is_resting(self, robot: int, now: int) -> bool:
        return now - self._path_start[robot] >= len(self._paths[robot]) - 1

    def set_goal(self, robot: int, start: int, goal: int, now: int) -> None:
        if not self.is_resting(robot, now) or self._paths[robot][-1] != start:
            raise PlanningError(f"robot {robot} is not resting on cell {start} at t={now}")
        self.table.unpark(start)
        started = time.perf_counter()
        path, expanded = spacetime_astar(
            self.layout, start, goal, now, self.table.constraints_for(goal)
        )
        self.plan_seconds.append(time.perf_counter() - started)
        self.plan_calls += 1
        self.expansions += expanded
        if path is None:
            raise PlanningError(f"no path for robot {robot} to cell {goal} at t={now}")
        self.table.reserve(robot, path, now)
        self._paths[robot] = path
        self._path_start[robot] = now

    def step(self, now: int, positions: Sequence[int]) -> list[int]:
        if now % PRUNE_PERIOD == 0:
            self.table.prune(now)
        return [self._cell_at(robot, now + 1) for robot in range(self.n_robots)]


MANAGERS: dict[str, type[TrafficManager]] = {
    NaiveManager.name: NaiveManager,
    ReservationManager.name: ReservationManager,
}
