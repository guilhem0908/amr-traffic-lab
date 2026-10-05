"""Exhaustive joint-state search: the optimality oracle for tiny instances.

It shares no code with Conflict-Based Search. It runs A* directly on the
product graph whose states are the positions of all agents, so its answer is
optimal by construction and its cost explodes with the number of agents. It
exists to cross-check CBS, not to be used.
"""

from __future__ import annotations

import heapq
from collections.abc import Sequence
from itertools import product

from .layout import UNREACHABLE, Layout

DEFAULT_STATE_BUDGET = 400_000

_RETIRE = -1
"""Pseudo-move: the agent stays on its goal for good and stops paying."""


def optimal_sum_of_costs(
    layout: Layout,
    starts: Sequence[int],
    goals: Sequence[int],
    state_budget: int = DEFAULT_STATE_BUDGET,
) -> int | None:
    """Optimal sum of costs of a one-shot instance, or ``None`` if it has no solution.

    State = (positions, set of retired agents). Each tick every active agent
    either moves or waits (cost 1) or, if it stands on its goal, retires
    (cost 0, it never moves again). The sum of costs of a plan is exactly the
    number of (agent, tick) pairs before retirement, including the ticks an
    agent spends on its goal before leaving it again. Heuristic: sum of the
    single-agent distances of the active agents, which is admissible.

    Raises ``RuntimeError`` if more than ``state_budget`` states are expanded.
    """
    n = len(starts)
    dist = [layout.distances_to(goal) for goal in goals]
    if any(dist[a][starts[a]] >= UNREACHABLE for a in range(n)):
        return None

    def heuristic(positions: tuple[int, ...], retired: int) -> int:
        return sum(dist[a][positions[a]] for a in range(n) if not retired >> a & 1)

    all_retired = (1 << n) - 1
    start = (tuple(starts), 0)
    best = {start: 0}
    heap = [(heuristic(*start), 0, start)]
    expanded = 0
    while heap:
        _, cost, state = heapq.heappop(heap)
        if cost > best[state]:
            continue
        positions, retired = state
        if retired == all_retired:
            return cost
        expanded += 1
        if expanded > state_budget:
            raise RuntimeError("joint-state search exceeded its state budget")
        options = []
        for agent in range(n):
            cell = positions[agent]
            if retired >> agent & 1:
                options.append((_RETIRE,))
                continue
            goal = goals[agent]
            moves = [cell, *(c for c in layout.neighbours[cell] if layout.aisle[c] or c == goal)]
            if cell == goal:
                moves.append(_RETIRE)
            options.append(tuple(moves))
        for choice in product(*options):
            after = tuple(positions[a] if choice[a] == _RETIRE else choice[a] for a in range(n))
            if len(set(after)) < n or _has_swap(positions, after):
                continue
            new_retired = retired
            step_cost = 0
            for agent in range(n):
                if choice[agent] == _RETIRE:
                    new_retired |= 1 << agent
                else:
                    step_cost += 1
            new_state = (after, new_retired)
            new_cost = cost + step_cost
            if new_cost < best.get(new_state, UNREACHABLE):
                best[new_state] = new_cost
                heapq.heappush(
                    heap, (new_cost + heuristic(after, new_retired), new_cost, new_state)
                )
    return None


def _has_swap(before: tuple[int, ...], after: tuple[int, ...]) -> bool:
    n = len(before)
    for a in range(n):
        if before[a] == after[a]:
            continue
        for b in range(a + 1, n):
            if after[a] == before[b] and after[b] == before[a]:
                return True
    return False
