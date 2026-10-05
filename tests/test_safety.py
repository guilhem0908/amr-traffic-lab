import pytest

from amr_traffic_lab.reservation import ReservationError, ReservationTable
from amr_traffic_lab.safety import (
    ILLEGAL_MOVE,
    SWAP,
    VERTEX,
    WRONG_ENDPOINT,
    check_transition,
    validate_paths,
)
from amr_traffic_lab.search import spacetime_astar
from conftest import make_layout

BLOCK = make_layout("..\n..")
ROW = make_layout("....#.")


def kinds(violations):
    return [violation.kind for violation in violations]


def test_two_robots_entering_the_same_cell_is_a_vertex_conflict():
    found = check_transition(ROW, [0, 2], [1, 1], time=7)
    assert kinds(found) == [VERTEX]
    assert found[0].time == 8 and found[0].robots == (0, 1) and found[0].cells == (1,)


def test_two_robots_exchanging_cells_is_a_swap_conflict():
    found = check_transition(ROW, [1, 2], [2, 1], time=3)
    assert kinds(found) == [SWAP]
    assert found[0].robots == (0, 1)


def test_following_and_rotating_are_allowed():
    assert check_transition(ROW, [0, 1, 2], [1, 2, 3]) == []
    assert check_transition(BLOCK, [0, 1, 3, 2], [1, 3, 2, 0]) == []


def test_teleporting_and_entering_a_wall_are_illegal_moves():
    assert kinds(check_transition(ROW, [0], [2])) == [ILLEGAL_MOVE]
    assert kinds(check_transition(ROW, [3], [4])) == [ILLEGAL_MOVE]
    assert kinds(check_transition(ROW, [3], [5])) == [ILLEGAL_MOVE]


def test_plan_validator_checks_endpoints_and_resting_robots():
    assert validate_paths(ROW, [0, 3], [1, 2], [[0, 1], [3, 2]]) == []
    assert kinds(validate_paths(ROW, [0, 3], [2, 2], [[0, 1], [3, 2]])) == [WRONG_ENDPOINT]
    # Robot 0 rests on cell 1 after t=1 and robot 1 drives into it at t=2.
    assert kinds(validate_paths(ROW, [0, 3], [1, 1], [[0, 1], [3, 2, 1]])) == [VERTEX]
    assert kinds(validate_paths(ROW, [0, 0], [1, 1], [[0, 1], [0, 1]])) == [VERTEX, VERTEX]


def test_reservations_block_the_cell_and_the_swap_but_not_the_follower():
    table = ReservationTable()
    table.reserve(robot=0, path=[1, 2, 3], start_time=0)
    constraints = table.constraints_for(goal=0)
    assert (2, 1) in constraints.vertex
    assert (2, 1, 0) in constraints.moves, "moving 2 -> 1 at t=0 would swap with robot 0"
    assert (0, 1, 0) not in constraints.moves, "following robot 0 into cell 1 is allowed"
    assert constraints.parked == {3: 2}
    assert constraints.last_time == 2


def test_a_robot_waits_in_the_bay_for_an_opposing_reserved_path():
    layout = make_layout(".....\n##.##")
    bay = layout.cell(1, 2)
    table = ReservationTable()
    table.reserve(0, [0, 1, 2, 3, 4], 0)
    path, _ = spacetime_astar(layout, bay, 0, 0, table.constraints_for(0))
    assert path == [bay, bay, bay, 2, 1, 0]
    table.reserve(1, path, 0)
    assert validate_paths(layout, [0, bay], [4, 0], [[0, 1, 2, 3, 4], path]) == []


def test_double_booking_a_cell_raises():
    table = ReservationTable()
    table.reserve(0, [1, 2], 0)
    with pytest.raises(ReservationError, match="robot 0"):
        table.reserve(1, [3, 2], 0)


def test_robot_may_settle_only_after_the_last_visit_of_its_goal():
    table = ReservationTable()
    table.reserve(0, [5, 5, 5, 6, 7], 10)
    table.unpark(7)
    assert table.constraints_for(goal=5).settle_from == 13
    assert table.constraints_for(goal=9).settle_from == 0


def test_pruning_drops_only_the_past():
    table = ReservationTable()
    table.reserve(0, [1, 2, 3, 4], 0)
    table.prune(now=2)
    assert set(table.vertex) == {(3, 2), (4, 3)}
    assert table.moves == {(4, 3, 2)}
    assert table.parked == {4: 3}
    assert table.constraints_for(goal=1).settle_from == 1
