import pytest

from amr_traffic_lab.cbs import (
    BUDGET_EXHAUSTED,
    FAILED,
    SOLVED,
    Conflict,
    find_conflicts,
    solve_cbs,
    solve_prioritised,
    sum_of_costs,
)
from amr_traffic_lab.exhaustive import optimal_sum_of_costs
from amr_traffic_lab.experiments import tiny_instance
from amr_traffic_lab.safety import validate_paths
from conftest import make_layout

# A five-cell corridor with one passing bay under its middle cell.
CORRIDOR_WITH_BAY = ".....\n##.##"


def cross_check(seed: int) -> str:
    """Compare CBS with the exhaustive search on one instance; return what happened."""
    layout, starts, goals = tiny_instance(seed)
    optimum = optimal_sum_of_costs(layout, starts, goals)
    result = solve_cbs(layout, starts, goals, node_budget=3000)
    if optimum is None:
        assert result.status in (BUDGET_EXHAUSTED, FAILED)
        return "unsolvable"
    if result.status == BUDGET_EXHAUSTED:
        return "budget"
    assert result.status == SOLVED
    assert validate_paths(layout, starts, goals, result.paths) == []
    assert result.cost == sum_of_costs(result.paths) == optimum
    return "optimal"


@pytest.mark.parametrize("seed", range(60))
def test_cbs_cost_equals_the_exhaustive_optimum_on_tiny_instances(seed):
    assert cross_check(seed) in ("optimal", "unsolvable")


@pytest.mark.slow
def test_cbs_is_optimal_whenever_it_finishes_on_four_hundred_instances():
    outcomes = [cross_check(seed) for seed in range(400)]
    solvable = len(outcomes) - outcomes.count("unsolvable")
    # CBS may run out of nodes on sliding-puzzle instances, but it must never
    # return a suboptimal plan (cross_check asserts that) and must rarely give up.
    assert solvable >= 300
    assert outcomes.count("budget") <= 0.02 * solvable


def test_head_on_meeting_costs_one_detour_and_one_wait():
    layout = make_layout(CORRIDOR_WITH_BAY)
    starts, goals = [0, 4], [4, 0]
    # Lower bound 4 + 4; one agent steps in and out of the bay (+2) and, as both
    # reach the middle at t=2, the other one waits a tick (+1).
    assert optimal_sum_of_costs(layout, starts, goals) == 11
    result = solve_cbs(layout, starts, goals)
    assert result.status == SOLVED and result.cost == 11
    assert validate_paths(layout, starts, goals, result.paths) == []


def test_agent_that_leaves_its_goal_pays_until_it_returns():
    layout = make_layout("....\n##.#")
    junction = layout.cell(0, 2)
    starts, goals = [junction, layout.cell(0, 0)], [junction, layout.cell(0, 3)]
    # The second agent needs 3 ticks; the first must step aside and is back at t=3.
    assert optimal_sum_of_costs(layout, starts, goals) == 6
    result = solve_cbs(layout, starts, goals)
    assert result.cost == 6
    assert len(result.paths[0]) - 1 == 3


def test_independent_agents_cost_the_sum_of_their_distances():
    layout = make_layout("....\n####\n....")
    starts, goals = [layout.cell(0, 0), layout.cell(2, 3)], [layout.cell(0, 3), layout.cell(2, 0)]
    assert optimal_sum_of_costs(layout, starts, goals) == 6
    result = solve_cbs(layout, starts, goals)
    assert (result.cost, result.high_level_nodes) == (6, 1)


def test_swap_in_a_dead_end_corridor_has_no_solution():
    layout = make_layout("...")
    assert optimal_sum_of_costs(layout, [0, 2], [2, 0]) is None
    result = solve_cbs(layout, [0, 2], [2, 0], node_budget=200)
    assert result.status == BUDGET_EXHAUSTED and result.paths is None


def test_agent_without_any_route_fails_immediately():
    layout = make_layout(".#.")
    result = solve_cbs(layout, [0], [2])
    assert result.status == FAILED and result.high_level_nodes == 0


def test_prioritised_planning_fails_where_cbs_succeeds():
    layout = make_layout("....\n##.#")
    junction = layout.cell(0, 2)
    # Agent 0 plans first and parks on the junction that agent 1 has to cross.
    starts, goals = [layout.cell(0, 1), layout.cell(0, 0)], [junction, layout.cell(0, 3)]
    assert solve_prioritised(layout, starts, goals).status == FAILED
    result = solve_cbs(layout, starts, goals)
    assert result.status == SOLVED
    assert result.cost == optimal_sum_of_costs(layout, starts, goals) == 6


@pytest.mark.parametrize("seed", range(80))
def test_prioritised_plans_are_valid_and_never_cheaper_than_cbs(seed):
    layout, starts, goals = tiny_instance(seed)
    prioritised = solve_prioritised(layout, starts, goals)
    if prioritised.status != SOLVED:
        return
    assert validate_paths(layout, starts, goals, prioritised.paths) == []
    optimal = solve_cbs(layout, starts, goals, node_budget=3000)
    assert optimal.status == SOLVED
    assert prioritised.cost >= optimal.cost


def test_find_conflicts_reports_vertex_swap_and_goal_conflicts():
    assert find_conflicts([[0, 1, 2], [5, 4, 3]]) == []
    assert find_conflicts([[0, 1], [2, 1]]) == [Conflict(1, 0, 1, 1)]
    assert find_conflicts([[0, 1], [1, 0]]) == [Conflict(0, 0, 1, 0, 1)]
    # Agent 0 rests on cell 1 from t=1; agent 1 drives through it at t=3.
    assert find_conflicts([[0, 1], [4, 3, 2, 1]]) == [Conflict(3, 0, 1, 1)]
    # Following a robot into the cell it leaves is not a conflict.
    assert find_conflicts([[1, 2, 3], [0, 1, 2]]) == []
