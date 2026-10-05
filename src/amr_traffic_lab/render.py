"""Replay recorded simulations as a side-by-side GIF (requires Pillow)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .layout import CHARGER, DROP, PICK, WALL, Layout
from .sim import LOADED, NO_GRIDLOCK, Frame, RobotState

SUPERSAMPLE = 2
"""Frames are drawn at this multiple of the output size, then downsampled (anti-aliasing)."""

# Geometry in output pixels.
CELL = 16
MARGIN = 8
GAP = 16
HEADER = 46
FOOTER = 30
SUBFRAMES = 2
FRAME_MS = 50
PALETTE_COLOURS = 64

# Font sizes in output pixels.
TITLE_SIZE = 15
CAPTION_SIZE = 11
COUNTER_SIZE = 26
BADGE_SIZE = 12
MAP_NAME_SIZE = 13

BACKGROUND = "#0e1319"
FLOOR = "#2a3442"
FLOOR_LINE = "#202935"
WALL_FILL = "#121820"
PICK_FILL = "#199e70"
DROP_FILL = "#9085e9"
CHARGER_FILL = "#3b4756"
ROBOT_EMPTY = "#e8ecf1"
ROBOT_PARKED = "#8491a3"
ROBOT_LOADED = "#f2b233"
ROBOT_EDGE = "#0e1319"
TEXT = "#f3f5f7"
TEXT_MUTED = "#9aa7b6"
ALERT = "#e66767"

STATION_FILL = {PICK: PICK_FILL, DROP: DROP_FILL, CHARGER: CHARGER_FILL}
FLAT_COLOURS = (
    BACKGROUND,
    FLOOR,
    FLOOR_LINE,
    WALL_FILL,
    PICK_FILL,
    DROP_FILL,
    CHARGER_FILL,
    ROBOT_EMPTY,
    ROBOT_PARKED,
    ROBOT_LOADED,
    TEXT,
    TEXT_MUTED,
    ALERT,
)
"""Colours that must survive the GIF palette unchanged."""


@dataclass(frozen=True)
class Panel:
    """One recorded run and how to caption it."""

    title: str
    subtitle: str
    accent: str
    frames: Sequence[Frame]
    gridlock_tick: int = NO_GRIDLOCK


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size * SUPERSAMPLE)


def _box(row: float, col: float, inset: float) -> tuple[float, float, float, float]:
    """Supersampled pixel box of the grid square at (row, col), shrunk by ``inset``."""
    size = CELL * SUPERSAMPLE
    return (
        col * size + inset,
        row * size + inset,
        (col + 1) * size - inset,
        (row + 1) * size - inset,
    )


def draw_map(layout: Layout) -> Image.Image:
    """Static floor plan at supersampled resolution."""
    size = CELL * SUPERSAMPLE
    image = Image.new("RGB", (layout.width * size, layout.height * size), WALL_FILL)
    draw = ImageDraw.Draw(image)
    for cell, kind in enumerate(layout.kinds):
        if kind == WALL:
            continue
        row, col = layout.row_col(cell)
        draw.rectangle(_box(row, col, 0), fill=FLOOR, outline=FLOOR_LINE)
        if kind in STATION_FILL:
            draw.rounded_rectangle(
                _box(row, col, 0.12 * size), radius=0.2 * size, fill=STATION_FILL[kind]
            )
    return image


def _draw_robots(image: Image.Image, layout: Layout, before: Frame, after: Frame, blend: float):
    draw = ImageDraw.Draw(image)
    size = CELL * SUPERSAMPLE
    for src, dst, state in zip(before.cells, after.cells, after.states, strict=True):
        src_row, src_col = layout.row_col(src)
        dst_row, dst_col = layout.row_col(dst)
        row = src_row + (dst_row - src_row) * blend
        col = src_col + (dst_col - src_col) * blend
        if state in LOADED:
            fill = ROBOT_LOADED
        elif state == RobotState.IDLE:
            fill = ROBOT_PARKED
        else:
            fill = ROBOT_EMPTY
        draw.ellipse(_box(row, col, 0.1 * size), fill=fill, outline=ROBOT_EDGE, width=2)


def _draw_header(draw: ImageDraw.ImageDraw, panel: Panel, x: int, width: int, tick: int) -> None:
    s = SUPERSAMPLE
    frame = panel.frames[min(tick, len(panel.frames) - 1)]
    draw.rounded_rectangle((x, 12 * s, x + 6 * s, 38 * s), radius=3 * s, fill=panel.accent)
    draw.text((x + 14 * s, 8 * s), panel.title, font=_font(TITLE_SIZE), fill=TEXT)
    draw.text((x + 14 * s, 28 * s), panel.subtitle, font=_font(CAPTION_SIZE), fill=TEXT_MUTED)
    counter = f"{frame.delivered}"
    counter_font = _font(COUNTER_SIZE)
    counter_width = draw.textlength(counter, font=counter_font)
    draw.text((x + width - counter_width, 4 * s), counter, font=counter_font, fill=TEXT)
    label = "orders delivered"
    label_width = draw.textlength(label, font=_font(CAPTION_SIZE))
    draw.text(
        (x + width - counter_width - label_width - 8 * s, 18 * s),
        label,
        font=_font(CAPTION_SIZE),
        fill=TEXT_MUTED,
    )


def _draw_gridlock_badge(image: Image.Image, panel: Panel, x: int, y: int, width: int) -> None:
    s = SUPERSAMPLE
    draw = ImageDraw.Draw(image)
    text = f"GRIDLOCK  no robot has moved since t = {panel.gridlock_tick} s"
    font = _font(BADGE_SIZE)
    text_width = draw.textlength(text, font=font)
    left = x + (width - text_width) / 2 - 10 * s
    draw.rounded_rectangle(
        (left, y, left + text_width + 20 * s, y + 24 * s), radius=6 * s, fill=ALERT
    )
    draw.text((left + 10 * s, y + 5 * s), text, font=font, fill=BACKGROUND)


def _draw_footer(draw: ImageDraw.ImageDraw, y: int, tick: int, total_width: int) -> None:
    s = SUPERSAMPLE
    font = _font(CAPTION_SIZE)
    x = MARGIN * s
    for fill, label, round_mark in (
        (PICK_FILL, "pick station", False),
        (DROP_FILL, "drop station", False),
        (CHARGER_FILL, "charger", False),
        (ROBOT_EMPTY, "robot, empty", True),
        (ROBOT_LOADED, "robot, loaded", True),
        (ROBOT_PARKED, "robot, idle", True),
    ):
        box = (x, y + 2 * s, x + 11 * s, y + 13 * s)
        if round_mark:
            draw.ellipse(box, fill=fill)
        else:
            draw.rounded_rectangle(box, radius=3 * s, fill=fill)
        draw.text((x + 16 * s, y), label, font=font, fill=TEXT_MUTED)
        x += (16 + 14) * s + int(draw.textlength(label, font=font))
    clock = f"t = {tick:3d} s"
    draw.text(
        (total_width - MARGIN * s - draw.textlength(clock, font=font), y),
        clock,
        font=font,
        fill=TEXT_MUTED,
    )


def render_side_by_side(
    layout: Layout, panels: Sequence[Panel], ticks: int, path: Path
) -> tuple[int, int, int]:
    """Write a looping GIF of ``ticks`` simulated seconds; returns (width, height, frames).

    A run that ended early (gridlock) is padded with its last frame, which is
    what the simulation would produce since a gridlock is a fixed point.
    """
    s = SUPERSAMPLE
    floor = draw_map(layout)
    panel_width = floor.width
    width = 2 * MARGIN * s + len(panels) * panel_width + (len(panels) - 1) * GAP * s
    height = (HEADER + FOOTER) * s + floor.height + MARGIN * s
    origins = [MARGIN * s + i * (panel_width + GAP * s) for i in range(len(panels))]

    images = []
    for step in range(ticks * SUBFRAMES):
        tick, sub = divmod(step, SUBFRAMES)
        canvas = Image.new("RGB", (width, height), BACKGROUND)
        draw = ImageDraw.Draw(canvas)
        for panel, x in zip(panels, origins, strict=True):
            last = len(panel.frames) - 1
            before, after = panel.frames[min(tick, last)], panel.frames[min(tick + 1, last)]
            view = floor.copy()
            _draw_robots(view, layout, before, after, sub / SUBFRAMES)
            canvas.paste(view, (x, HEADER * s))
            _draw_header(draw, panel, x, panel_width, tick)
            if panel.gridlock_tick != NO_GRIDLOCK and tick >= panel.gridlock_tick:
                badge_y = HEADER * s + floor.height - 34 * s
                _draw_gridlock_badge(canvas, panel, x, badge_y, panel_width)
        _draw_footer(draw, HEADER * s + floor.height + 8 * s, tick, width)
        images.append(canvas.resize((width // s, height // s), Image.Resampling.LANCZOS))

    palette = _shared_palette(images[0], images[-1], [panel.accent for panel in panels])
    indexed = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image in images]
    path.parent.mkdir(parents=True, exist_ok=True)
    indexed[0].save(
        path,
        save_all=True,
        append_images=indexed[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=False,
    )
    return images[0].width, images[0].height, len(images)


def _shared_palette(first: Image.Image, last: Image.Image, accents: Sequence[str]) -> Image.Image:
    """One palette for every frame: the flat colours, exactly, plus anti-aliasing blends.

    The flat colours take the first entries as they are; the remaining entries
    come from a median-cut quantisation of the first and last frames.
    """
    exact = [ImageColor.getrgb(colour) for colour in (*FLAT_COLOURS, *accents)]
    reference = Image.new("RGB", (first.width, first.height + last.height))
    reference.paste(first, (0, 0))
    reference.paste(last, (0, first.height))
    blends = reference.quantize(PALETTE_COLOURS - len(exact), method=Image.Quantize.MEDIANCUT)
    palette = Image.new("P", (1, 1))
    palette.putpalette([channel for colour in exact for channel in colour] + blends.getpalette())
    return palette


def render_layouts(layouts: Sequence[Layout], path: Path) -> None:
    """One PNG with the floor plans stacked, each with its name."""
    s = SUPERSAMPLE
    maps = [draw_map(layout) for layout in layouts]
    label_height = 22 * s
    width = max(m.width for m in maps) + 2 * MARGIN * s
    height = sum(m.height + label_height for m in maps) + (len(maps) + 1) * MARGIN * s
    canvas = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    y = MARGIN * s
    for layout, floor in zip(layouts, maps, strict=True):
        draw.text((MARGIN * s, y), layout.name, font=_font(MAP_NAME_SIZE), fill=TEXT)
        canvas.paste(floor, (MARGIN * s, y + label_height))
        y += label_height + floor.height + MARGIN * s
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.resize((width // s, height // s), Image.Resampling.LANCZOS).save(path)
