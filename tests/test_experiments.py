import csv
from pathlib import Path

import pytest

from amr_traffic_lab import experiments as ex
from amr_traffic_lab.cbs import SOLVED
from amr_traffic_lab.cli import main
from amr_traffic_lab.layout import load_layout

RESULTS = Path(__file__).resolve().parent.parent / "results"


def summary_row(robots, throughput, layout="open_floor", manager="reservation"):
    return {"layout": layout, "manager": manager, "robots": robots, "throughput_mean": throughput}


def run_row(delivered, gridlock_tick, order_ticks):
    return {
        "layout": "open_floor",
        "manager": "naive",
        "robots": 4,
        "seed": 0,
        "ticks": 1800,
        "delivered": delivered,
        "order_ticks": order_ticks,
        "gridlock_tick": gridlock_tick,
        "wait_ticks": 10,
        "travel_ticks": 100,
        "violations": 0,
        "safety_checks": 1800,
    }


def test_saturation_is_the_smallest_fleet_within_five_percent_of_the_best():
    summary = [
        summary_row(2, 100.0),
        summary_row(4, 189.9),
        summary_row(6, 190.0),
        summary_row(8, 200.0),
        summary_row(10, 199.0),
        summary_row(8, 999.0, manager="naive"),
    ]
    assert ex.saturation_fleet(summary, "open_floor", "reservation")["robots"] == 6
    assert ex.best_fleet(summary, "open_floor", "reservation")["robots"] == 8


def test_best_fleet_prefers_the_smaller_fleet_on_a_tie():
    summary = [summary_row(4, 50.0), summary_row(8, 50.0)]
    assert ex.best_fleet(summary, "open_floor", "reservation")["robots"] == 4


def test_summary_scales_to_orders_per_hour_and_counts_gridlocks():
    rows = [run_row(10, -1, 400), run_row(20, 300, 900), run_row(0, 100, 0)]
    (summary,) = ex.summarise_lifelong(rows)
    assert summary["runs"] == 3 and summary["delivered"] == 30
    assert summary["throughput_mean"] == 20.0, "30 orders in three half-hour runs"
    assert (summary["throughput_min"], summary["throughput_max"]) == (0.0, 40.0)
    assert summary["gridlocked_runs"] == 2 and summary["median_gridlock_s"] == 200
    assert summary["order_time_mean_s"] == pytest.approx(1300 / 30, abs=0.05)
    assert summary["wait_share"] == 0.1


def test_oneshot_instances_are_reproducible_and_use_distinct_aisle_cells():
    layout = load_layout("narrow_aisles")
    starts, goals = ex.oneshot_instance(layout, 12, seed=5)
    assert (starts, goals) == ex.oneshot_instance(layout, 12, seed=5)
    assert (starts, goals) != ex.oneshot_instance(layout, 12, seed=6)
    assert len(set(starts)) == len(set(goals)) == 12
    assert all(layout.aisle[cell] for cell in starts + goals)


def test_oneshot_job_reports_valid_plans_at_or_above_the_lower_bound():
    row = ex.run_oneshot_job(ex.OneshotJob("open_floor", 6, 3))
    assert row["cbs_status"] == row["prioritised_status"] == SOLVED
    assert row["invalid_plans"] == 0
    assert row["lower_bound"] <= row["cbs_cost"] <= row["prioritised_cost"]


def test_crosscheck_counts_add_up_and_show_no_mismatch():
    report = ex.crosscheck(instances=40)
    assert report["mismatches"] == 0
    assert report["cbs_finished"] == report["cbs_equal_to_optimum"]
    assert report["cbs_finished"] + report["cbs_out_of_budget"] == report["solvable"] <= 40
    assert 0 < report["cbs_needed_a_split"] < report["cbs_finished"]


def committed_rows(name):
    with (RESULTS / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.mark.parametrize(
    "key",
    [
        ("open_floor", "naive", "8", "0"),
        ("open_floor", "reservation", "4", "3"),
        ("narrow_aisles", "naive", "12", "1"),
        ("narrow_aisles", "reservation", "6", "2"),
        ("single_corridor", "naive", "2", "0"),
        ("single_corridor", "reservation", "3", "1"),
    ],
)
def test_committed_lifelong_rows_are_reproduced_exactly(key):
    committed = {
        (r["layout"], r["manager"], r["robots"], r["seed"]): r
        for r in committed_rows("lifelong_runs.csv")
    }
    layout, manager, robots, seed = key
    fresh = ex.run_lifelong_job(ex.LifelongJob(layout, manager, int(robots), int(seed)))
    assert {name: str(value) for name, value in fresh.items()} == committed[key]


def test_committed_summary_matches_the_committed_runs():
    recomputed = ex.summarise_lifelong(committed_rows("lifelong_runs.csv"))
    as_text = [{name: str(value) for name, value in row.items()} for row in recomputed]
    assert as_text == committed_rows("lifelong_summary.csv")


def test_no_committed_run_has_a_safety_violation():
    rows = committed_rows("lifelong_runs.csv")
    assert len(rows) == len(ex.lifelong_jobs())
    assert sum(int(row["violations"]) for row in rows) == 0
    assert all(int(row["safety_checks"]) > 0 for row in rows)


def test_simulate_command_prints_the_shift_summary(capsys):
    assert main(["simulate", "--layout", "open_floor", "--robots", "3", "--seconds", "300"]) == 0
    printed = capsys.readouterr().out
    assert "open_floor, reservation manager, 3 robots, seed 0, 300 s" in printed
    assert "safety check      300 ticks, 0 violations" in printed


def test_layouts_command_prints_every_floor_plan(capsys):
    assert main(["layouts"]) == 0
    printed = capsys.readouterr().out
    for name in ("open_floor", "narrow_aisles", "single_corridor"):
        assert name in printed and load_layout(name).to_text() in printed
