"""Independent safety checks, so that no planner is its own judge.

The functions below know nothing about how a move was chosen. They restate
the two classical multi-agent path finding conflicts (Stern et al. 2019):

    vertex conflict   two robots occupy the same cell at the same time
    swap conflict     two robots exchange cells during the same tick

and add a kinematic check (a robot stays or moves to a free 4-neighbour).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .layout import WALL, Layout

VERTEX = "vertex"
SWAP = "swap"
ILLEGAL_MOVE = "illegal_move"
WRONG_ENDPOINT = "wrong_endpoint"


@dataclass(frozen=True)
class Violation:
    kind: str
    time: int
    robots: tuple[int, ...]
    cells: tuple[int, ...]


class SafetyViolation(RuntimeError):
    """Raised by the simulator when a manager that claims safety breaks it."""


def check_transition(
    layout: Layout, before: Sequence[int], after: Sequence[int], time: int = 0
) -> list[Violation]:
    """Return every violation in the joint move ``before -> after`` of one tick."""
    violations = []
    for robot, (src, dst) in enumerate(zip(before, after, strict=True)):
        legal = dst == src or dst in layout.neighbours[src]
        if not legal or layout.kinds[dst] == WALL:
            violations.append(Violation(ILLEGAL_MOVE, time, (robot,), (src, dst)))

    holder: dict[int, int] = {}
    for robot, cell in enumerate(after):
        if cell in holder:
            violations.append(Violation(VERTEX, time + 1, (holder[cell], robot), (cell,)))
        else:
            holder[cell] = robot

    moving = {
        (src, dst): robot
        for robot, (src, dst) in enumerate(zip(before, after, strict=True))
        if src != dst
    }
    for (src, dst), robot in moving.items():
        other = moving.get((dst, src))
        if other is not None and robot < other:
            violations.append(Violation(SWAP, time, (robot, other), (src, dst)))
    return violations


def validate_paths(
    layout: Layout, starts: Sequence[int], goals: Sequence[int], paths: Sequence[Sequence[int]]
) -> list[Violation]:
    """Check a one-shot plan: right endpoints, legal moves, no conflict, robots rest at goals."""
    violations = []
    for robot, path in enumerate(paths):
        if path[0] != starts[robot] or path[-1] != goals[robot]:
            violations.append(Violation(WRONG_ENDPOINT, 0, (robot,), (path[0], path[-1])))
    horizon = max(len(path) for path in paths)
    joint = [[path[min(t, len(path) - 1)] for path in paths] for t in range(horizon)]
    if len(set(joint[0])) != len(joint[0]):
        violations.append(Violation(VERTEX, 0, tuple(range(len(paths))), tuple(joint[0])))
    for time in range(horizon - 1):
        violations.extend(check_transition(layout, joint[time], joint[time + 1], time))
    return violations
