import itertools
import random

import pytest

from amr_traffic_lab.layout import UNREACHABLE
from amr_traffic_lab.search import (
    AISLE_WAIT_EFFORT,
    MOVE_EFFORT,
    Constraints,
    shortest_path,
    spacetime_astar,
)
from conftest import make_layout, random_open_grid

INFINITE = float("inf")


def brute_force_lexicographic(layout, start, goal, start_time, constraints, horizon):
    """Layer-by-layer dynamic programme over every (cell, time): no heuristic, no pruning.

    Returns ``(arrival_time, effort)`` of the best path or ``None``.
    """
    layer = {start: 0}
    for time in range(start_time, horizon):
        if goal in layer and time >= constraints.settle_from:
            return time, layer[goal]
        following = {}
        for cell, effort in layer.items():
            for other in (cell, *layout.neighbours[cell]):
                if other != cell and other != goal and not layout.aisle[other]:
                    continue
                if (other, time + 1) in constraints.vertex:
                    continue
                if (cell, other, time) in constraints.moves:
                    continue
                if time + 1 >= constraints.parked.get(other, INFINITE):
                    continue
                if other != cell:
                    step = MOVE_EFFORT
                else:
                    step = AISLE_WAIT_EFFORT if layout.aisle[cell] else 0
                if effort + step < following.get(other, INFINITE):
                    following[other] = effort + step
        layer = following
    return None


def effort_of(layout, path):
    total = 0
    for here, there in itertools.pairwise(path):
        if here != there:
            total += MOVE_EFFORT
        elif layout.aisle[here]:
            total += AISLE_WAIT_EFFORT
    return total


def assert_path_respects(layout, path, start, goal, start_time, constraints):
    assert path[0] == start and path[-1] == goal
    for offset, (here, there) in enumerate(itertools.pairwise(path)):
        time = start_time + offset
        assert there == here or there in layout.neighbours[here]
        assert (there, time + 1) not in constraints.vertex
        assert (here, there, time) not in constraints.moves
        assert time + 1 < constraints.parked.get(there, INFINITE)
    assert start_time + len(path) - 1 >= constraints.settle_from


def random_constraints(rng, layout, cells, goal, start, start_time):
    span = 12
    vertex = {
        (rng.choice(cells), start_time + rng.randrange(1, span))
        for _ in range(rng.randrange(0, 14))
    }
    moves = set()
    for _ in range(rng.randrange(0, 8)):
        src = rng.choice(cells)
        if layout.neighbours[src]:
            moves.add((src, rng.choice(layout.neighbours[src]), start_time + rng.randrange(span)))
    parked = {}
    for cell in rng.sample(cells, k=min(len(cells), rng.randrange(0, 3))):
        if cell not in (start, goal):
            parked[cell] = start_time + rng.randrange(0, span)
    last_time = max(
        [t for _, t in vertex] + [t + 1 for _, _, t in moves] + list(parked.values()) + [-1]
    )
    settle_from = max([t for cell, t in vertex if cell == goal], default=-1) + 1
    return Constraints(vertex, moves, parked, last_time, settle_from)


@pytest.mark.parametrize("seed", range(300))
def test_spacetime_astar_matches_brute_force_on_random_constrained_grids(seed):
    rng = random.Random(seed)
    layout = random_open_grid(rng, rng.randrange(2, 6), rng.randrange(2, 6), wall_share=0.2)
    cells = list(layout.free)
    start, goal = rng.choice(cells), rng.choice(cells)
    start_time = rng.randrange(0, 5)
    constraints = random_constraints(rng, layout, cells, goal, start, start_time)

    path, _ = spacetime_astar(layout, start, goal, start_time, constraints)
    horizon = max(constraints.last_time, start_time) + len(cells) + 3
    expected = brute_force_lexicographic(layout, start, goal, start_time, constraints, horizon)

    if expected is None:
        assert path is None
    else:
        assert path is not None
        assert_path_respects(layout, path, start, goal, start_time, constraints)
        assert (start_time + len(path) - 1, effort_of(layout, path)) == expected


def test_unconstrained_spacetime_path_is_a_static_shortest_path(shipped_layout):
    rng = random.Random(3)
    for _ in range(40):
        start, goal = rng.sample(shipped_layout.endpoints, 2)
        path, _ = spacetime_astar(shipped_layout, start, goal, 17, Constraints())
        assert len(path) - 1 == shipped_layout.distances_to(goal)[start]
        assert len(set(path)) == len(path)


def test_robot_waits_in_its_bay_rather_than_in_the_aisle():
    layout = make_layout("C....P")
    start, goal = layout.cell(0, 0), layout.cell(0, 5)
    # Another robot holds the far end of the aisle until t=6.
    vertex = {(layout.cell(0, 4), t) for t in range(7)}
    path, _ = spacetime_astar(layout, start, goal, 0, Constraints(vertex=vertex, last_time=6))
    assert len(path) - 1 == 8
    waits = [here for here, there in itertools.pairwise(path) if here == there]
    assert waits == [start] * 3


def test_no_path_when_parked_robots_wall_off_the_goal():
    layout = make_layout(".....")
    constraints = Constraints(parked={2: 0}, last_time=0)
    path, expansions = spacetime_astar(layout, 0, 4, 0, constraints)
    assert path is None
    assert expansions <= 2 * len(layout.free)


def test_settling_is_delayed_until_the_goal_is_no_longer_crossed():
    layout = make_layout("...")
    constraints = Constraints(vertex={(2, 5)}, last_time=5, settle_from=6)
    path, _ = spacetime_astar(layout, 0, 2, 0, constraints)
    assert len(path) - 1 == 6
    assert path[5] != 2


def test_shortest_path_length_equals_the_distance_table(shipped_layout):
    rng = random.Random(11)
    for _ in range(60):
        start, goal = rng.sample(shipped_layout.endpoints, 2)
        path = shortest_path(shipped_layout, start, goal)
        assert path[0] == start and path[-1] == goal
        assert len(path) - 1 == shipped_layout.distances_to(goal)[start]
        assert all(shipped_layout.aisle[cell] for cell in path[1:-1])


def test_shortest_path_detours_around_blocked_cells_or_gives_up():
    layout = make_layout("...\n.#.\n...")
    direct = shortest_path(layout, layout.cell(0, 0), layout.cell(0, 2))
    assert direct == [0, 1, 2]
    detour = shortest_path(layout, layout.cell(0, 0), layout.cell(0, 2), blocked={1})
    assert len(detour) - 1 == 6 and 1 not in detour
    assert shortest_path(layout, layout.cell(0, 0), layout.cell(0, 2), blocked={1, 3}) is None


def test_unreachable_goal_returns_none():
    layout = make_layout(".#.")
    assert layout.distances_to(2)[0] == UNREACHABLE
    assert shortest_path(layout, 0, 2) is None
    assert spacetime_astar(layout, 0, 2, 0, Constraints()) == (None, 0)
