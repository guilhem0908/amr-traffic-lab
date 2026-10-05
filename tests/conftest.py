"""Shared helpers: tiny hand-written layouts and seeded random grids."""

from __future__ import annotations

import random

import pytest

from amr_traffic_lab.layout import LAYOUT_NAMES, Layout, load_layout


def make_layout(text: str) -> Layout:
    return Layout.from_text("test", text)


def random_open_grid(rng: random.Random, rows: int, cols: int, wall_share: float) -> Layout:
    """A grid of aisle cells with random walls (it may be disconnected)."""
    lines = [
        "".join("#" if rng.random() < wall_share else "." for _ in range(cols)) for _ in range(rows)
    ]
    if all("." not in line for line in lines):
        lines[0] = "." + lines[0][1:]
    return Layout("random", lines)


@pytest.fixture(params=LAYOUT_NAMES)
def shipped_layout(request) -> Layout:
    return load_layout(request.param)
