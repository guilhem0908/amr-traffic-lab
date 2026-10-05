"""Factory-floor layouts parsed from plain-text maps.

A map is a rectangle of characters, one per grid cell:

    #  wall, rack or machine (blocked)
    .  aisle cell (robots travel here)
    P  pick station    D  drop station    C  charger / parking bay

Stations and chargers are *endpoints*: cells where a robot may rest. The whole
package applies one transit rule: a robot drives on aisle cells only and enters
an endpoint only when it is the goal of the current trip. That is the
"well-formed infrastructure" of Čáp et al. (2015) and Ma et al. (2017): any
endpoint can be reached from any other without crossing a third one, so a
resting robot never blocks a route.
"""

from __future__ import annotations

from collections import deque
from importlib import resources

WALL = "#"
AISLE = "."
PICK = "P"
DROP = "D"
CHARGER = "C"
ENDPOINT_KINDS = (PICK, DROP, CHARGER)
VALID_KINDS = (WALL, AISLE, *ENDPOINT_KINDS)

UNREACHABLE = 10**9
"""Distance reported for a cell that has no legal route to the goal."""

LAYOUT_NAMES = ("open_floor", "narrow_aisles", "single_corridor")
"""Maps shipped with the package, from least to most constrained."""

_FOUR_NEIGHBOURS = ((-1, 0), (1, 0), (0, -1), (0, 1))


class LayoutError(ValueError):
    """Raised when a text map is malformed or not well-formed for a fleet."""


class Layout:
    """A 4-connected grid with typed cells, addressed by ``row * width + col``."""

    def __init__(self, name: str, rows: list[str]) -> None:
        if not rows or not rows[0]:
            raise LayoutError("a layout needs at least one non-empty row")
        width = len(rows[0])
        for index, row in enumerate(rows):
            if len(row) != width:
                raise LayoutError(f"row {index} has {len(row)} cells, expected {width}")
            unknown = set(row) - set(VALID_KINDS)
            if unknown:
                raise LayoutError(f"row {index} contains unknown symbols {sorted(unknown)}")

        self.name = name
        self.width = width
        self.height = len(rows)
        self.kinds: tuple[str, ...] = tuple("".join(rows))
        self.free: tuple[int, ...] = tuple(i for i, k in enumerate(self.kinds) if k != WALL)
        self.aisle: tuple[bool, ...] = tuple(k == AISLE for k in self.kinds)
        self.picks = self._cells_of(PICK)
        self.drops = self._cells_of(DROP)
        self.chargers = self._cells_of(CHARGER)
        self.endpoints: tuple[int, ...] = tuple(
            i for i, k in enumerate(self.kinds) if k in ENDPOINT_KINDS
        )
        self.neighbours: tuple[tuple[int, ...], ...] = tuple(
            self._free_neighbours(cell) for cell in range(len(self.kinds))
        )
        self._distance_cache: dict[int, tuple[int, ...]] = {}

    @classmethod
    def from_text(cls, name: str, text: str) -> Layout:
        """Parse a map; blank lines around it are ignored."""
        return cls(name, [line for line in text.splitlines() if line.strip()])

    def _cells_of(self, kind: str) -> tuple[int, ...]:
        return tuple(i for i, k in enumerate(self.kinds) if k == kind)

    def _free_neighbours(self, cell: int) -> tuple[int, ...]:
        if self.kinds[cell] == WALL:
            return ()
        row, col = divmod(cell, self.width)
        found = []
        for d_row, d_col in _FOUR_NEIGHBOURS:
            r, c = row + d_row, col + d_col
            if 0 <= r < self.height and 0 <= c < self.width:
                other = r * self.width + c
                if self.kinds[other] != WALL:
                    found.append(other)
        return tuple(found)

    def cell(self, row: int, col: int) -> int:
        return row * self.width + col

    def row_col(self, cell: int) -> tuple[int, int]:
        return divmod(cell, self.width)

    def distances_to(self, goal: int) -> tuple[int, ...]:
        """Length of the shortest legal route from every cell to ``goal``.

        Backward breadth-first search under the transit rule: the route may
        start anywhere, then uses aisle cells only until it steps onto ``goal``.
        The result is the perfect heuristic h* of the single-robot problem, so
        it is admissible and consistent for every search in this package
        (adding robots or constraints can only lengthen a route).
        """
        cached = self._distance_cache.get(goal)
        if cached is not None:
            return cached
        dist = [UNREACHABLE] * len(self.kinds)
        dist[goal] = 0
        queue = deque([goal])
        while queue:
            cell = queue.popleft()
            for other in self.neighbours[cell]:
                if dist[other] == UNREACHABLE:
                    dist[other] = dist[cell] + 1
                    if self.aisle[other]:
                        queue.append(other)
        result = tuple(dist)
        self._distance_cache[goal] = result
        return result

    def well_formed_problems(self, n_robots: int) -> list[str]:
        """Return why this layout cannot host ``n_robots`` (empty list if it can).

        Conditions of a well-formed pickup-and-delivery instance (Ma et al.
        2017) restated for this grid: the aisle cells form one
        connected component, every endpoint touches an aisle cell, and there
        are at least as many parking endpoints (chargers) as robots.
        """
        problems = []
        aisle_cells = [i for i, is_aisle in enumerate(self.aisle) if is_aisle]
        if not aisle_cells:
            return ["the map has no aisle cell"]
        seen = {aisle_cells[0]}
        queue = deque(seen)
        while queue:
            cell = queue.popleft()
            for other in self.neighbours[cell]:
                if self.aisle[other] and other not in seen:
                    seen.add(other)
                    queue.append(other)
        if len(seen) != len(aisle_cells):
            problems.append("aisle cells are not all connected")
        for endpoint in self.endpoints:
            if not any(self.aisle[n] for n in self.neighbours[endpoint]):
                problems.append(f"endpoint at {self.row_col(endpoint)} touches no aisle cell")
        if not self.picks or not self.drops:
            problems.append("the map needs at least one pick and one drop station")
        if len(self.chargers) < n_robots:
            problems.append(f"{len(self.chargers)} chargers cannot park {n_robots} robots")
        return problems

    def require_well_formed(self, n_robots: int) -> None:
        problems = self.well_formed_problems(n_robots)
        if problems:
            raise LayoutError(f"layout '{self.name}': " + "; ".join(problems))

    def to_text(self) -> str:
        rows = [
            "".join(self.kinds[r * self.width : (r + 1) * self.width]) for r in range(self.height)
        ]
        return "\n".join(rows)


def load_layout(name: str) -> Layout:
    """Load one of the maps shipped in ``amr_traffic_lab/maps``."""
    if name not in LAYOUT_NAMES:
        raise LayoutError(f"unknown layout '{name}', choose one of {LAYOUT_NAMES}")
    text = resources.files("amr_traffic_lab").joinpath("maps", f"{name}.txt").read_text("utf-8")
    return Layout.from_text(name, text)
