import random

import pytest

from amr_traffic_lab.layout import (
    LAYOUT_NAMES,
    UNREACHABLE,
    Layout,
    LayoutError,
    load_layout,
)
from conftest import make_layout

MAX_FLEET = 16


def reference_distances(layout: Layout, goal: int) -> list[int]:
    """Bellman-Ford on the directed transit graph: a step may enter aisle cells or the goal."""
    dist = [UNREACHABLE] * len(layout.kinds)
    dist[goal] = 0
    for _ in range(len(layout.free)):
        changed = False
        for cell in layout.free:
            for other in layout.neighbours[cell]:
                may_enter = layout.aisle[other] or other == goal
                if may_enter and dist[other] + 1 < dist[cell]:
                    dist[cell] = dist[other] + 1
                    changed = True
        if not changed:
            break
    return dist


def test_shipped_layouts_are_well_formed_for_the_largest_fleet(shipped_layout):
    assert shipped_layout.well_formed_problems(MAX_FLEET) == []


def test_shipped_layouts_offer_the_same_stations(shipped_layout):
    counts = (len(shipped_layout.picks), len(shipped_layout.drops), len(shipped_layout.chargers))
    assert counts == (8, 8, 16)


def test_distances_match_bellman_ford_under_the_transit_rule(shipped_layout):
    rng = random.Random(7)
    goals = list(shipped_layout.endpoints) + rng.sample(
        [c for c in shipped_layout.free if shipped_layout.aisle[c]], 10
    )
    for goal in goals:
        assert list(shipped_layout.distances_to(goal)) == reference_distances(shipped_layout, goal)


def test_a_route_never_crosses_a_foreign_endpoint():
    layout = make_layout("C.C\n#.#\n#P#")
    left, right, pick = layout.cell(0, 0), layout.cell(0, 2), layout.cell(2, 1)
    assert layout.distances_to(pick)[left] == 3
    assert layout.distances_to(right)[left] == 2
    blocked_by_bay = make_layout("CCP")
    assert blocked_by_bay.distances_to(blocked_by_bay.cell(0, 2))[0] == UNREACHABLE


def test_text_round_trip_preserves_the_map(shipped_layout):
    again = Layout.from_text(shipped_layout.name, shipped_layout.to_text())
    assert again.kinds == shipped_layout.kinds
    assert (again.width, again.height) == (shipped_layout.width, shipped_layout.height)


def test_rows_of_unequal_length_are_rejected():
    with pytest.raises(LayoutError, match="row 1"):
        make_layout("...\n..")


def test_unknown_symbols_are_rejected():
    with pytest.raises(LayoutError, match="unknown symbols"):
        make_layout(".X.")


def test_unknown_layout_name_is_rejected():
    with pytest.raises(LayoutError, match="unknown layout"):
        load_layout("moon_base")


def test_too_few_chargers_is_reported():
    layout = make_layout("P.D\nC.#")
    assert layout.well_formed_problems(1) == []
    assert any("chargers" in problem for problem in layout.well_formed_problems(2))
    with pytest.raises(LayoutError):
        layout.require_well_formed(2)


def test_disconnected_aisles_are_reported():
    layout = make_layout("P.#.D\nC###C")
    assert any("not all connected" in problem for problem in layout.well_formed_problems(1))


def test_endpoint_without_aisle_access_is_reported():
    layout = make_layout("P.D\n#.#\nCC#")
    assert any("touches no aisle" in problem for problem in layout.well_formed_problems(1))


def test_shipped_layout_names_all_load():
    assert [load_layout(name).name for name in LAYOUT_NAMES] == list(LAYOUT_NAMES)
