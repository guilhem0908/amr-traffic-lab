import random

import pytest

from amr_traffic_lab.managers import (
    NaiveManager,
    PlanningError,
    ReservationManager,
    _grant_moves,
)
from amr_traffic_lab.safety import check_transition
from conftest import make_layout

# Two bays on each side of a single-lane aisle (row 1). No passing place.
SINGLE_LANE = "C####C\n......\nD####P"
ROW = make_layout("......")
BLOCK = make_layout("..\n..")


def drive(manager, layout, starts, goals, ticks):
    """Send every robot to its goal at t=0 and return the positions after each tick."""
    manager.reset(layout, starts, seed=0)
    for robot, (start, goal) in enumerate(zip(starts, goals, strict=True)):
        manager.set_goal(robot, start, goal, now=0)
    history = [list(starts)]
    for now in range(ticks):
        after = manager.step(now, history[-1])
        assert check_transition(layout, history[-1], after, now) == []
        history.append(after)
    return history


def single_lane_trips():
    layout = make_layout(SINGLE_LANE)
    starts = [layout.cell(0, 0), layout.cell(0, 5)]
    goals = [layout.cell(2, 5), layout.cell(2, 0)]
    return layout, starts, goals


def test_naive_robots_block_each_other_head_on_in_a_single_lane():
    layout, starts, goals = single_lane_trips()
    history = drive(NaiveManager(), layout, starts, goals, ticks=120)
    assert history[-1] != goals
    assert history[-1] == history[-60], "nobody has moved for a minute: this is a deadlock"
    assert {layout.row_col(cell)[0] for cell in history[-1]} == {1}, "both are stuck in the aisle"


def test_reservation_manager_solves_the_same_head_on_trips():
    layout, starts, goals = single_lane_trips()
    history = drive(ReservationManager(), layout, starts, goals, ticks=20)
    assert history[-1] == goals
    first_arrival = next(t for t, cells in enumerate(history) if cells[0] == goals[0])
    second_arrival = next(t for t, cells in enumerate(history) if cells[1] == goals[1])
    assert first_arrival == 7, "the robot that plans first drives straight through"
    assert second_arrival == 13, "the other one waits in its bay for six ticks, then drives"
    assert history[6][1] == starts[1] and history[7][1] != starts[1]


def test_naive_robot_replans_around_a_parked_obstacle():
    layout = make_layout("C...P\n#...#")
    manager = NaiveManager()
    manager.reset(layout, [layout.cell(0, 0), layout.cell(0, 2)], seed=0)
    manager.set_goal(0, layout.cell(0, 0), layout.cell(0, 4), now=0)
    cells = [layout.cell(0, 0), layout.cell(0, 2)]
    for now in range(20):
        cells = manager.step(now, cells)
    assert cells == [layout.cell(0, 4), layout.cell(0, 2)]


def test_reserved_trip_cannot_be_replanned_before_it_ends():
    layout, starts, goals = single_lane_trips()
    manager = ReservationManager()
    manager.reset(layout, starts, seed=0)
    manager.set_goal(0, starts[0], goals[0], now=0)
    with pytest.raises(PlanningError, match="not resting"):
        manager.set_goal(0, starts[0], goals[1], now=3)


def test_reservation_manager_refuses_a_goal_held_by_a_parked_robot():
    layout, starts, _ = single_lane_trips()
    manager = ReservationManager()
    manager.reset(layout, starts, seed=0)
    with pytest.raises(PlanningError, match="no path"):
        manager.set_goal(0, starts[0], starts[1], now=0)


def test_right_of_way_blocks_head_on_and_lets_followers_through():
    assert _grant_moves([2, 3], [3, 2]) == set(), "head-on swap"
    assert _grant_moves([0, 1, 2], [1, 2, 3]) == {0, 1, 2}, "platoon moves together"
    assert _grant_moves([0, 1, 2], [1, 2, 2]) == set(), "platoon stops behind a stopped robot"
    assert _grant_moves([0, 2], [1, 1]) == {0}, "same target: the smaller index goes"
    assert _grant_moves([0, 1, 3, 2], [1, 3, 2, 0]) == {0, 1, 2, 3}, "rotation is legal"


@pytest.mark.parametrize("seed", range(200))
def test_right_of_way_never_lets_a_conflict_through(seed):
    rng = random.Random(seed)
    layout = make_layout("\n".join("." * 5 for _ in range(4)))
    robots = rng.randrange(2, 12)
    before = rng.sample(layout.free, robots)
    wanted = [rng.choice((cell, *layout.neighbours[cell])) for cell in before]
    granted = _grant_moves(before, wanted)
    after = [wanted[r] if r in granted else before[r] for r in range(robots)]
    assert check_transition(layout, before, after) == []
    # Maximality: a refused robot could not have moved on top of the granted moves.
    for robot in set(range(robots)) - granted:
        if wanted[robot] == before[robot]:
            continue
        retry = list(after)
        retry[robot] = wanted[robot]
        lower_index_rival = any(
            wanted[other] == wanted[robot] and other < robot and wanted[other] != before[other]
            for other in range(robots)
        )
        assert check_transition(layout, before, retry) != [] or lower_index_rival
