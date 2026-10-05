"""Single-robot searches: grid A* and space-time A* under constraints.

Both use ``Layout.distances_to(goal)`` as heuristic. It is the exact
single-robot distance, hence admissible and consistent, so the first time a
state is popped its cost is optimal (Hart, Nilsson and Raphael 1968).
"""

from __future__ import annotations

import heapq
from collections.abc import Collection, Container, Mapping
from dataclasses import dataclass, field

from .layout import UNREACHABLE, Layout


@dataclass(frozen=True)
class Constraints:
    """What one robot must avoid in space-time.

    vertex       ``(cell, t)`` pairs the robot may not occupy.
    moves        ``(from_cell, to_cell, t)``: the robot may not leave
                 ``from_cell`` at time ``t`` to be in ``to_cell`` at ``t + 1``.
    parked       ``cell -> t0``: the cell is occupied for every ``t >= t0``.
    last_time    no vertex constraint is later than this and no constrained
                 move arrives later than this; afterwards the world is static.
    settle_from  earliest time at which the robot may arrive at its goal and
                 stay there for good.
    """

    vertex: Container[tuple[int, int]] = frozenset()
    moves: Container[tuple[int, int, int]] = frozenset()
    parked: Mapping[int, int] = field(default_factory=dict)
    last_time: int = -1
    settle_from: int = 0


def shortest_path(
    layout: Layout, start: int, goal: int, blocked: Collection[int] = frozenset()
) -> list[int] | None:
    """A* on the static grid; returns ``[start, ..., goal]`` or ``None``.

    ``blocked`` cells are treated as extra walls (the goal itself is never
    blocked). Ties on f = g + h are broken towards the smaller h, then the
    smaller cell index, which makes the route deterministic.
    """
    h = layout.distances_to(goal)
    if h[start] >= UNREACHABLE:
        return None
    parent: dict[int, int | None] = {start: None}
    cost = {start: 0}
    closed: set[int] = set()
    heap = [(h[start], h[start], start)]
    while heap:
        _, _, cell = heapq.heappop(heap)
        if cell == goal:
            return _walk_back(parent, cell)
        if cell in closed:
            continue
        closed.add(cell)
        g = cost[cell] + 1
        for other in layout.neighbours[cell]:
            if other != goal and (not layout.aisle[other] or other in blocked):
                continue
            if g < cost.get(other, UNREACHABLE):
                cost[other] = g
                parent[other] = cell
                heapq.heappush(heap, (g + h[other], h[other], other))
    return None


def _walk_back(parent: Mapping[int, int | None], cell: int) -> list[int]:
    path = [cell]
    while (previous := parent[path[-1]]) is not None:
        path.append(previous)
    path.reverse()
    return path


MOVE_EFFORT = 2
AISLE_WAIT_EFFORT = 1
"""Tie-breaking effort of one tick: move 2, wait on an aisle cell 1, wait on an endpoint 0."""


def spacetime_astar(
    layout: Layout, start: int, goal: int, start_time: int, constraints: Constraints
) -> tuple[list[int] | None, int]:
    """Earliest-arrival path in (cell, time) space (Silver 2005).

    Returns ``(path, expansions)`` where ``path[i]`` is the cell occupied at
    ``start_time + i`` and the last cell is ``goal``, reached at a time
    ``>= constraints.settle_from`` so the robot can rest there for good.
    ``path`` is ``None`` when no such path exists.

    State = (cell, t); actions = wait or move to a 4-neighbour, one tick each.
    The cost is lexicographic: first the arrival time, then an *effort* that
    charges 2 per move, 1 per tick waited on an aisle cell and 0 per tick
    waited on an endpoint. Among all earliest-arrival paths the search thus
    returns one that does not wander and that waits in its bay rather than in
    the aisle. With d = static distance to the goal, the heuristic (d, 2d) is
    consistent for both components (a tick lowers d by at most 1 and a move
    costs effort 2), so a state is final the first time it is popped.

    Termination without an arbitrary horizon: for ``t > last_time`` no
    time-stamped constraint remains, so all states that share a cell are
    equivalent up to a time shift and are merged under one key. The search
    space is therefore bounded by ``cells * (last_time - start_time + 2)``.
    """
    h = layout.distances_to(goal)
    if h[start] >= UNREACHABLE:
        return None, 0
    vertex, moves, parked = constraints.vertex, constraints.moves, constraints.parked
    settle_from = constraints.settle_from
    static_from = max(constraints.last_time + 1, settle_from, start_time)
    aisle, neighbours = layout.aisle, layout.neighbours

    parent: dict[tuple[int, int], int | None] = {(start, start_time): None}
    best = {(start, min(start_time, static_from)): (start_time, 0)}
    heap = [(start_time + h[start], MOVE_EFFORT * h[start], -start_time, start)]
    expansions = 0
    while heap:
        _, f_effort, negative_time, cell = heapq.heappop(heap)
        time = -negative_time
        effort = f_effort - MOVE_EFFORT * h[cell]
        if best[(cell, min(time, static_from))] != (time, effort):
            continue
        if cell == goal and time >= settle_from:
            return _walk_back_in_time(parent, cell, time), expansions
        expansions += 1
        next_time = time + 1
        key_time = min(next_time, static_from)
        wait_effort = effort + (AISLE_WAIT_EFFORT if aisle[cell] else 0)
        for other in (cell, *neighbours[cell]):
            if other != cell and other != goal and not aisle[other]:
                continue
            if (other, next_time) in vertex or (cell, other, time) in moves:
                continue
            parked_since = parked.get(other)
            if parked_since is not None and next_time >= parked_since:
                continue
            candidate = (next_time, wait_effort if other == cell else effort + MOVE_EFFORT)
            key = (other, key_time)
            known = best.get(key)
            if known is not None and known <= candidate:
                continue
            best[key] = candidate
            parent[(other, next_time)] = cell
            heapq.heappush(
                heap,
                (next_time + h[other], candidate[1] + MOVE_EFFORT * h[other], -next_time, other),
            )
    return None, expansions


def _walk_back_in_time(
    parent: Mapping[tuple[int, int], int | None], cell: int, time: int
) -> list[int]:
    path = [cell]
    while (previous := parent[(path[-1], time)]) is not None:
        path.append(previous)
        time -= 1
    path.reverse()
    return path
