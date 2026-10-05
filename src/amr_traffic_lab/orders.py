"""Transport orders: carry one load from a pick station to a drop station."""

from __future__ import annotations

import random
from dataclasses import dataclass

from .layout import Layout


@dataclass
class Order:
    """One pick-and-drop job; the time fields are simulation ticks."""

    order_id: int
    pick: int
    drop: int
    robot: int | None = None
    assigned: int | None = None
    delivered: int | None = None


class OrderStream:
    """Seeded source of orders: pick and drop stations drawn uniformly and independently.

    The same seed always yields the same sequence, whatever the traffic
    manager does, so two managers are compared on identical demand.
    """

    def __init__(self, layout: Layout, seed: int) -> None:
        self._picks = layout.picks
        self._drops = layout.drops
        self._rng = random.Random(seed)
        self._next_id = 0

    def release(self) -> Order:
        pick = self._picks[self._rng.randrange(len(self._picks))]
        drop = self._drops[self._rng.randrange(len(self._drops))]
        order = Order(self._next_id, pick, drop)
        self._next_id += 1
        return order
