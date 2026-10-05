"""One-shot multi-agent path finding: Conflict-Based Search and prioritised planning.

Problem (Stern et al. 2019): every agent has a start and a goal cell, moves or
waits once per tick, and stays on its goal after arriving. A solution has no
vertex conflict and no swap conflict. The cost of an agent is the time at
which it reaches its goal for the last time; the objective is the sum of
costs (SOC).
"""

from __future__ import annotations

import heapq
from collections.abc import Sequence
from dataclasses import dataclass

from .layout import Layout
from .reservation import ReservationTable
from .search import Constraints, spacetime_astar

SOLVED = "solved"
BUDGET_EXHAUSTED = "budget"
FAILED = "failed"

DEFAULT_NODE_BUDGET = 2000
"""High-level nodes CBS may expand before giving up (it cannot detect infeasibility)."""


@dataclass(frozen=True)
class Conflict:
    """First agent at ``cell`` at ``time``; for a swap it moves on to ``other``."""

    time: int
    first: int
    second: int
    cell: int
    other: int | None = None


@dataclass(frozen=True)
class MapfResult:
    status: str
    paths: list[list[int]] | None
    cost: int | None
    high_level_nodes: int
    low_level_expansions: int


def sum_of_costs(paths: Sequence[Sequence[int]]) -> int:
    return sum(len(path) - 1 for path in paths)


def find_conflicts(paths: Sequence[Sequence[int]]) -> list[Conflict]:
    """All vertex and swap conflicts, earliest first; agents rest on their last cell."""
    conflicts = []
    horizon = max(len(path) for path in paths)
    for time in range(horizon):
        here = [path[min(time, len(path) - 1)] for path in paths]
        after = [path[min(time + 1, len(path) - 1)] for path in paths]
        holder: dict[int, int] = {}
        for agent, cell in enumerate(here):
            if cell in holder:
                conflicts.append(Conflict(time, holder[cell], agent, cell))
            else:
                holder[cell] = agent
        moving = {
            (a, b): agent for agent, (a, b) in enumerate(zip(here, after, strict=True)) if a != b
        }
        for (src, dst), agent in moving.items():
            opposite = moving.get((dst, src))
            if opposite is not None and agent < opposite:
                conflicts.append(Conflict(time, agent, opposite, src, dst))
    return conflicts


def _constraints(
    vertex: frozenset[tuple[int, int]], moves: frozenset[tuple[int, int, int]], goal: int
) -> Constraints:
    last_time = max(
        max((t for _, t in vertex), default=-1),
        max((t + 1 for _, _, t in moves), default=-1),
    )
    settle_from = max((t for cell, t in vertex if cell == goal), default=-1) + 1
    return Constraints(vertex=vertex, moves=moves, last_time=last_time, settle_from=settle_from)


def solve_cbs(
    layout: Layout,
    starts: Sequence[int],
    goals: Sequence[int],
    node_budget: int = DEFAULT_NODE_BUDGET,
) -> MapfResult:
    """Conflict-Based Search (Sharon, Stern, Felner and Sturtevant 2015).

    High level: best-first search on a binary constraint tree. A node holds one
    constraint set per agent and the cheapest path of each agent under its
    set; its cost is the SOC of these paths. The node with the lowest cost is
    popped; if its paths conflict, the earliest conflict (a, b, cell, t) is
    split into two children, one forbidding it to ``a`` and one to ``b``.

    Optimality: any conflict-free solution satisfies the constraints of at
    least one child, a node's cost is a lower bound on every solution below
    it, and nodes are popped by increasing cost. The first conflict-free node
    popped is therefore SOC-optimal. Ties are broken towards fewer conflicts.
    """
    n = len(starts)
    empty: frozenset = frozenset()
    low_level = 0
    paths = []
    for agent in range(n):
        path, expanded = spacetime_astar(layout, starts[agent], goals[agent], 0, Constraints())
        low_level += expanded
        if path is None:
            return MapfResult(FAILED, None, None, 0, low_level)
        paths.append(path)

    root_sets = tuple((empty, empty) for _ in range(n))
    conflicts = find_conflicts(paths)
    serial = 0
    heap = [(sum_of_costs(paths), len(conflicts), serial, root_sets, paths, conflicts)]
    expanded_nodes = 0
    while heap and expanded_nodes < node_budget:
        cost, _, _, sets, paths, conflicts = heapq.heappop(heap)
        expanded_nodes += 1
        if not conflicts:
            return MapfResult(SOLVED, paths, cost, expanded_nodes, low_level)
        conflict = conflicts[0]
        for agent, new_vertex, new_move in _split(conflict):
            vertex, moves = sets[agent]
            if new_vertex is not None:
                vertex = vertex | {new_vertex}
            if new_move is not None:
                moves = moves | {new_move}
            path, expanded = spacetime_astar(
                layout, starts[agent], goals[agent], 0, _constraints(vertex, moves, goals[agent])
            )
            low_level += expanded
            if path is None:
                continue
            child_sets = (*sets[:agent], (vertex, moves), *sets[agent + 1 :])
            child_paths = [*paths[:agent], path, *paths[agent + 1 :]]
            child_conflicts = find_conflicts(child_paths)
            serial += 1
            heapq.heappush(
                heap,
                (
                    sum_of_costs(child_paths),
                    len(child_conflicts),
                    serial,
                    child_sets,
                    child_paths,
                    child_conflicts,
                ),
            )
    status = BUDGET_EXHAUSTED if heap else FAILED
    return MapfResult(status, None, None, expanded_nodes, low_level)


def _split(conflict: Conflict):
    """Yield ``(agent, vertex_constraint, move_constraint)`` for the two children."""
    if conflict.other is None:
        yield conflict.first, (conflict.cell, conflict.time), None
        yield conflict.second, (conflict.cell, conflict.time), None
    else:
        yield conflict.first, None, (conflict.cell, conflict.other, conflict.time)
        yield conflict.second, None, (conflict.other, conflict.cell, conflict.time)


def solve_prioritised(layout: Layout, starts: Sequence[int], goals: Sequence[int]) -> MapfResult:
    """Prioritised planning (Erdmann and Lozano-Perez 1987; Silver 2005).

    Agents plan one after the other, in index order, each avoiding the paths
    already written in the reservation table. Fast, but neither complete nor
    optimal: an early agent may park on the only route of a later one.
    """
    table = ReservationTable()
    paths: list[list[int]] = []
    low_level = 0
    for agent, (start, goal) in enumerate(zip(starts, goals, strict=True)):
        path, expanded = spacetime_astar(layout, start, goal, 0, table.constraints_for(goal))
        low_level += expanded
        if path is None:
            return MapfResult(FAILED, None, None, agent, low_level)
        table.reserve(agent, path, 0)
        paths.append(path)
    return MapfResult(SOLVED, paths, sum_of_costs(paths), len(paths), low_level)
