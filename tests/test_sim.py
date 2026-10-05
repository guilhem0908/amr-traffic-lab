import itertools

import pytest

from amr_traffic_lab.layout import CHARGER, LAYOUT_NAMES, load_layout
from amr_traffic_lab.managers import (
    MANAGERS,
    NaiveManager,
    ReservationManager,
    TrafficManager,
)
from amr_traffic_lab.safety import SafetyViolation
from amr_traffic_lab.search import shortest_path
from amr_traffic_lab.sim import (
    BOOK_SIZE,
    EN_ROUTE,
    NO_GRIDLOCK,
    STALL_LIMIT,
    FleetSim,
    RobotState,
    spread_starts,
)


class BlindManager(TrafficManager):
    """Independent shortest paths with no right-of-way rule at all: the unsafe strawman."""

    name = "blind"

    def reset(self, layout, starts, seed):
        super().reset(layout, starts, seed)
        self._routes = [[] for _ in starts]

    def set_goal(self, robot, start, goal, now):
        self._routes[robot] = shortest_path(self.layout, start, goal)[:0:-1]

    def step(self, now, positions):
        return [
            route.pop() if route else cell
            for route, cell in zip(self._routes, positions, strict=True)
        ]


def recount_conflicts(frames):
    """Count vertex and swap conflicts straight from the recorded cells."""
    vertex = swap = 0
    for before, after in itertools.pairwise(frames):
        vertex += len(after.cells) - len(set(after.cells))
        moves = {(a, b) for a, b in zip(before.cells, after.cells, strict=True) if a != b}
        swap += sum((b, a) in moves for a, b in moves) // 2
    return vertex, swap


@pytest.mark.parametrize("layout_name", LAYOUT_NAMES)
@pytest.mark.parametrize("manager_name", sorted(MANAGERS))
@pytest.mark.parametrize("robots", [5, 16])
def test_no_vertex_or_swap_conflict_over_thousands_of_ticks(layout_name, manager_name, robots):
    ticks = 400
    for seed in (0, 1, 2):
        sim = FleetSim(
            load_layout(layout_name), MANAGERS[manager_name](), robots, seed, record_frames=True
        )
        result = sim.run(ticks)
        assert result.violations == 0
        assert result.safety_checks == len(sim.frames) - 1
        assert recount_conflicts(sim.frames) == (0, 0)
        if manager_name == ReservationManager.name:
            assert result.safety_checks == ticks and result.gridlock_tick == NO_GRIDLOCK


def test_a_manager_without_right_of_way_is_caught_by_the_safety_check():
    layout = load_layout("single_corridor")
    sim = FleetSim(layout, BlindManager(), 8, seed=0, record_frames=True)
    with pytest.raises(SafetyViolation, match="blind manager"):
        sim.run(400)
    assert sim.violations, "the violation is recorded before the simulator raises"


def test_the_independent_recount_sees_the_conflicts_of_the_blind_manager():
    blind = BlindManager()
    blind.claims_safety = False
    sim = FleetSim(load_layout("single_corridor"), blind, 8, seed=0, record_frames=True)
    result = sim.run(400)
    vertex, swap = recount_conflicts(sim.frames)
    assert vertex + swap > 0
    assert result.violations >= vertex + swap


def test_oldest_active_trip_advances_on_every_tick(shipped_layout):
    """Liveness of the reservation manager: see docs/design.md, section 'Why it cannot gridlock'."""
    manager = ReservationManager()
    sim = FleetSim(shipped_layout, manager, 16, seed=3)
    planned_at = {}
    plan = manager.set_goal

    def recording_set_goal(robot, start, goal, now):
        plan(robot, start, goal, now)
        planned_at[robot] = len(planned_at_log)
        planned_at_log.append(robot)

    planned_at_log = []
    manager.set_goal = recording_set_goal
    checked = 0
    for _ in range(600):
        before = [robot.cell for robot in sim.robots]
        sim.tick()
        active = [r for r in sim.robots if r.state in EN_ROUTE and before[r.index] != r.goal]
        if active:
            oldest = min(active, key=lambda r: planned_at[r.index])
            assert oldest.cell != before[oldest.index]
            checked += 1
    assert checked > 500


def test_naive_fleet_gridlocks_in_the_single_corridor_and_reservations_do_not():
    layout = load_layout("single_corridor")
    naive = FleetSim(layout, NaiveManager(), 12, seed=0).run(1200)
    reserved = FleetSim(layout, ReservationManager(), 12, seed=0).run(1200)
    assert naive.gridlock_tick != NO_GRIDLOCK
    assert reserved.gridlock_tick == NO_GRIDLOCK
    assert reserved.delivered > 10 * max(naive.delivered, 1)


def test_a_gridlock_is_a_fixed_point_of_the_simulation():
    sim = FleetSim(load_layout("single_corridor"), NaiveManager(), 6, seed=1)
    result = sim.run(3600)
    assert result.gridlock_tick != NO_GRIDLOCK
    assert sim.now == result.gridlock_tick + STALL_LIMIT
    frozen = [robot.cell for robot in sim.robots]
    delivered = result.delivered
    for _ in range(300):
        sim.tick()
    assert [robot.cell for robot in sim.robots] == frozen
    assert len(sim.delivered) == delivered


def test_one_robot_behaves_identically_under_both_managers(shipped_layout):
    runs = [
        FleetSim(shipped_layout, MANAGERS[name](), 1, seed=5, record_frames=True)
        for name in sorted(MANAGERS)
    ]
    results = [sim.run(900) for sim in runs]
    assert runs[0].frames == runs[1].frames
    assert results[0].delivered == results[1].delivered > 0
    assert results[0].wait_ticks == 0


def test_runs_are_reproducible_and_depend_on_the_seed(shipped_layout):
    def frames(seed):
        sim = FleetSim(shipped_layout, ReservationManager(), 6, seed, record_frames=True)
        sim.run(300)
        return sim.frames

    assert frames(4) == frames(4)
    assert frames(4) != frames(5)


@pytest.mark.parametrize("manager_name", sorted(MANAGERS))
def test_station_locks_and_order_accounting_hold_at_every_tick(shipped_layout, manager_name):
    sim = FleetSim(shipped_layout, MANAGERS[manager_name](), 12, seed=2)
    endpoints = set(shipped_layout.endpoints)
    for _ in range(500):
        sim.tick()
        goals = [robot.goal for robot in sim.robots if robot.state in EN_ROUTE]
        assert len(goals) == len(set(goals)), "two robots are heading for the same bay"
        resting = [robot.cell for robot in sim.robots if robot.state not in EN_ROUTE]
        assert set(resting) <= endpoints, "a robot without a trip must rest on an endpoint"
        assert not set(goals) & set(resting), "a robot is sent to an occupied bay"
        for robot in sim.robots:
            assert sim.claims[robot.cell if robot.state not in EN_ROUTE else robot.goal] == (
                robot.index
            )
        assert len(sim.book) <= BOOK_SIZE
    in_progress = [robot.order for robot in sim.robots if robot.order is not None]
    ids = [order.order_id for order in sim.delivered + in_progress + sim.book]
    assert sorted(ids) == list(range(len(ids))), "every released order is in exactly one place"
    assert all(0 <= order.assigned < order.delivered for order in sim.delivered)


def test_idle_robots_end_up_on_chargers(shipped_layout):
    sim = FleetSim(shipped_layout, ReservationManager(), 16, seed=0)
    for _ in range(400):
        sim.tick()
        for robot in sim.robots:
            if robot.state == RobotState.IDLE:
                assert shipped_layout.kinds[robot.cell] == CHARGER


def test_spread_starts_are_distinct_chargers(shipped_layout):
    for robots in range(1, 17):
        starts = spread_starts(shipped_layout, robots)
        assert len(set(starts)) == robots
        assert set(starts) <= set(shipped_layout.chargers)


def test_a_fleet_larger_than_the_charger_row_is_refused(shipped_layout):
    with pytest.raises(ValueError, match="chargers cannot park"):
        FleetSim(shipped_layout, ReservationManager(), 17, seed=0)
