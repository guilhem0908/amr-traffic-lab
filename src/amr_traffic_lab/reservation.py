"""Reservation table: the committed space-time paths of the fleet.

This is the shared structure of cooperative A* (Silver 2005), called the
*token* in the Token Passing algorithm of Ma et al. (2017). A robot that plans
reads it as a set of constraints, then writes its own path into it.
"""

from __future__ import annotations

from .search import Constraints


class ReservationError(RuntimeError):
    """Raised when a path is written over an existing reservation."""


class ReservationTable:
    """Vertex reservations, forbidden swaps and parked robots.

    ``vertex[(cell, t)]`` is the robot holding ``cell`` at time ``t``.
    ``moves`` holds, for each reserved move ``u -> v`` leaving at ``t``, the
    reverse move ``(v, u, t)``: taking it would swap two robots through each
    other. Following a robot into the cell it leaves is allowed.
    ``parked[cell] = t0`` marks a robot resting on ``cell`` from ``t0`` on.
    """

    def __init__(self) -> None:
        self.vertex: dict[tuple[int, int], int] = {}
        self.moves: set[tuple[int, int, int]] = set()
        self.parked: dict[int, int] = {}
        self.last_time = 0
        self._last_visit: dict[int, int] = {}

    def park(self, cell: int, since: int) -> None:
        self.parked[cell] = since

    def unpark(self, cell: int) -> None:
        """Forget that a robot rests on ``cell`` (it is about to plan a trip)."""
        self.parked.pop(cell, None)

    def reserve(self, robot: int, path: list[int], start_time: int) -> None:
        """Commit ``path`` (one cell per tick from ``start_time``), then park at its end."""
        for offset, cell in enumerate(path):
            key = (cell, start_time + offset)
            holder = self.vertex.get(key)
            if holder is not None and holder != robot:
                raise ReservationError(f"cell {cell} at t={key[1]} is held by robot {holder}")
            self.vertex[key] = robot
            if start_time + offset > self._last_visit.get(cell, -1):
                self._last_visit[cell] = start_time + offset
        for offset in range(len(path) - 1):
            if path[offset] != path[offset + 1]:
                self.moves.add((path[offset + 1], path[offset], start_time + offset))
        end_time = start_time + len(path) - 1
        self.parked[path[-1]] = end_time
        self.last_time = max(self.last_time, end_time)

    def constraints_for(self, goal: int) -> Constraints:
        """Constraints seen by a robot that wants to travel to ``goal`` and rest there.

        The robot may settle only after the last reserved visit of ``goal``.
        """
        return Constraints(
            vertex=self.vertex,
            moves=self.moves,
            parked=self.parked,
            last_time=self.last_time,
            settle_from=self._last_visit.get(goal, -1) + 1,
        )

    def prune(self, now: int) -> None:
        """Drop reservations that lie strictly in the past."""
        self.vertex = {key: robot for key, robot in self.vertex.items() if key[1] >= now}
        self.moves = {move for move in self.moves if move[2] >= now}
