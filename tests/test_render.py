import pytest

pytest.importorskip("PIL")
pytest.importorskip("matplotlib")

from PIL import Image, ImageColor

from amr_traffic_lab import plots, render
from amr_traffic_lab.layout import LAYOUT_NAMES, load_layout
from amr_traffic_lab.managers import MANAGERS
from amr_traffic_lab.sim import FleetSim

MAX_WIDTH = 900
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def test_floor_plan_paints_every_station_in_its_colour(shipped_layout):
    image = render.draw_map(shipped_layout)
    size = render.CELL * render.SUPERSAMPLE
    assert image.size == (shipped_layout.width * size, shipped_layout.height * size)
    for cells, colour in (
        (shipped_layout.picks, render.PICK_FILL),
        (shipped_layout.drops, render.DROP_FILL),
        (shipped_layout.chargers, render.CHARGER_FILL),
    ):
        for cell in cells:
            row, col = shipped_layout.row_col(cell)
            centre = (col * size + size // 2, row * size + size // 2)
            assert image.getpixel(centre) == ImageColor.getrgb(colour)


def test_side_by_side_gif_has_two_subframes_per_tick_and_fits_a_readme(tmp_path):
    layout = load_layout("single_corridor")
    panels = []
    for name in sorted(MANAGERS):
        sim = FleetSim(layout, MANAGERS[name](), 6, seed=0, record_frames=True)
        result = sim.run(8)
        panels.append(render.Panel(name, "caption", "#3987e5", sim.frames, result.gridlock_tick))
    path = tmp_path / "clip.gif"
    width, height, frames = render.render_side_by_side(layout, panels, 6, path)
    with Image.open(path) as clip:
        assert clip.size == (width, height) and width <= MAX_WIDTH
        assert clip.n_frames == frames == 6 * render.SUBFRAMES
        colours = {colour for _, colour in clip.convert("RGB").getcolors(maxcolors=1 << 16)}
    for flat in (render.PICK_FILL, render.DROP_FILL, render.ROBOT_PARKED, render.BACKGROUND):
        wanted = ImageColor.getrgb(flat)
        nearest = min(sum(abs(a - b) for a, b in zip(c, wanted, strict=True)) for c in colours)
        assert nearest <= 6, f"{flat} was lost by the shared palette"


def test_figures_are_written_as_png(tmp_path):
    summary = [
        {
            "layout": layout,
            "manager": manager,
            "robots": robots,
            "runs": 4,
            "throughput_mean": 30.0 * robots,
            "throughput_min": 25.0 * robots,
            "throughput_max": 35.0 * robots,
            "gridlocked_runs": 0 if manager == "reservation" else min(4, robots // 4),
        }
        for layout in LAYOUT_NAMES
        for manager in sorted(MANAGERS)
        for robots in (1, 4, 8, 16)
    ]
    oneshot = [
        {
            "layout": layout,
            "agents": agents,
            "instances": 25,
            "cbs_solved": 25 - agents,
            "prioritised_solved": 25,
        }
        for layout in ("open_floor", "narrow_aisles")
        for agents in (2, 6, 12)
    ]
    plots.plot_lifelong(summary, tmp_path / "throughput.png")
    plots.plot_oneshot(oneshot, tmp_path / "oneshot.png")
    render.render_layouts([load_layout(name) for name in LAYOUT_NAMES], tmp_path / "layouts.png")
    for name in ("throughput.png", "oneshot.png", "layouts.png"):
        assert (tmp_path / name).read_bytes().startswith(PNG_SIGNATURE)
