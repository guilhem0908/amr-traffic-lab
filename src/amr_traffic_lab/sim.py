"""Lifelong fleet simulator: orders, dispatch, one tick at a time.

One tick is one second: a cell is 1 m and a robot drives at 1 m/s, so an hour
of factory time is 3,600 ticks. The simulator owns the task logic (orders,
station locks, dispatch, parking) and delegates motion to a traffic manager.
After every tick it runs the independent safety check of ``safety.py``.
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass

from .layout import CHARGER, Layout
from .managers import TrafficManager
from .orders import Order, OrderStream
from .safety import SafetyViolation, Violation, check_transition

SECONDS_PER_TICK = 1.0
TICKS_PER_HOUR = int(3600 / SECONDS_PER_TICK)

HANDLING_TICKS = 2
"""Ticks a robot stays docked to load or unload."""

BOOK_SIZE = 8
"""Open orders visible to the dispatcher; the book is refilled as soon as one is assigned."""

STALL_LIMIT = 60
"""Ticks without any robot moving, while some robot has a trip, that count as a gridlock."""


class RobotState(enum.Enum):
    IDLE = "idle"
    TO_PICK = "to_pick"
    LOADING = "loading"
    TO_DROP = "to_drop"
    UNLOADING = "unloading"
    TO_PARK = "to_park"


EN_ROUTE = frozenset({RobotState.TO_PICK, RobotState.TO_DROP, RobotState.TO_PARK})
LOADED = frozenset({RobotState.LOADING, RobotState.TO_DROP, RobotState.UNLOADING})


@dataclass
class Robot:
    index: int
    cell: int
    state: RobotState = RobotState.IDLE
    order: Order | None = None
    goal: int | None = None
    busy_until: int = 0


@dataclass(frozen=True)
class Frame:
    """What the renderer needs to draw one tick."""

    cells: tuple[int, ...]
    states: tuple[RobotState, ...]
    delivered: int


@dataclass(frozen=True)
class RunResult:
    layout: str
    manager: str
    robots: int
    seed: int
    ticks: int
    delivered: int
    order_ticks: int
    gridlock_tick: int
    wait_ticks: int
    travel_ticks: int
    plan_calls: int
    expansions: int
    safety_checks: int
    violations: int

    def as_row(self) -> dict[str, int | str]:
        return asdict(self)


NO_GRIDLOCK = -1


def spread_starts(layout: Layout, n_robots: int) -> list[int]:
    """Start cells: ``n_robots`` chargers spread evenly over the charger list."""
    chargers = layout.chargers
    return [chargers[i * len(chargers) // n_robots] for i in range(n_robots)]


class FleetSim:
    """A fleet serving an endless, seeded backlog of pick-and-drop orders.

    Station lock: a station belongs to one robot from the moment the robot is
    sent to it until the robot has physically left it, so two robots never
    head for the same bay. An order is assigned when its pick station is free;
    the loaded robot leaves the pick station only once the drop station is
    free (first come, first served). A robot that finishes on a drop station
    and gets no new order drives to the nearest free charger, which frees the
    station. These are the task rules of Token Passing (Ma et al. 2017).
    """

    def __init__(
        self,
        layout: Layout,
        manager: TrafficManager,
        n_robots: int,
        seed: int,
        *,
        record_frames: bool = False,
    ) -> None:
        layout.require_well_formed(n_robots)
        self.layout = layout
        self.manager = manager
        self.seed = seed
        self.now = 0
        self.robots = [Robot(i, cell) for i, cell in enumerate(spread_starts(layout, n_robots))]
        self.claims: dict[int, int] = {robot.cell: robot.index for robot in self.robots}
        self.book: list[Order] = []
        self.delivered: list[Order] = []
        self.gridlock_tick = NO_GRIDLOCK
        self.wait_ticks = 0
        self.travel_ticks = 0
        self.safety_checks = 0
        self.violations: list[Violation] = []
        self._stream = OrderStream(layout, seed)
        self._stalled = 0
        self.frames: list[Frame] | None = [] if record_frames else None
        manager.reset(layout, [robot.cell for robot in self.robots], seed)
        self._record_frame()

    def run(self, ticks: int) -> RunResult:
        """Simulate until ``ticks`` or until a gridlock, which is a fixed point of the update."""
        while self.now < ticks and self.gridlock_tick == NO_GRIDLOCK:
            self.tick()
        return self.result(ticks)

    def result(self, ticks: int) -> RunResult:
        return RunResult(
            layout=self.layout.name,
            manager=self.manager.name,
            robots=len(self.robots),
            seed=self.seed,
            ticks=ticks,
            delivered=len(self.delivered),
            order_ticks=sum(order.delivered - order.assigned for order in self.delivered),
            gridlock_tick=self.gridlock_tick,
            wait_ticks=self.wait_ticks,
            travel_ticks=self.travel_ticks,
            plan_calls=self.manager.plan_calls,
            expansions=self.manager.expansions,
            safety_checks=self.safety_checks,
            violations=len(self.violations),
        )

    def tick(self) -> None:
        now = self.now
        self._register_arrivals(now)
        self._release_claims()
        self._start_deliveries(now)
        self._dispatch(now)
        self._park_idle_robots(now)

        before = [robot.cell for robot in self.robots]
        after = self.manager.step(now, before)
        found = check_transition(self.layout, before, after, now)
        self.safety_checks += 1
        if found:
            self.violations.extend(found)
            if self.manager.claims_safety:
                raise SafetyViolation(f"{self.manager.name} manager at t={now}: {found[0]}")

        someone_moved = False
        someone_travels = False
        for robot, cell in zip(self.robots, after, strict=True):
            if robot.state in EN_ROUTE:
                someone_travels = True
                self.travel_ticks += 1
                if cell == robot.cell:
                    self.wait_ticks += 1
            if cell != robot.cell:
                someone_moved = True
                robot.cell = cell
        self._stalled = self._stalled + 1 if someone_travels and not someone_moved else 0
        if self._stalled >= STALL_LIMIT and self.gridlock_tick == NO_GRIDLOCK:
            self.gridlock_tick = now + 1 - STALL_LIMIT
        self.now = now + 1
        self._record_frame()

    def _register_arrivals(self, now: int) -> None:
        for robot in self.robots:
            if robot.state == RobotState.TO_PICK and robot.cell == robot.order.pick:
                robot.state = RobotState.LOADING
                robot.busy_until = now + HANDLING_TICKS
            elif robot.state == RobotState.TO_DROP and robot.cell == robot.order.drop:
                robot.state = RobotState.UNLOADING
                robot.busy_until = now + HANDLING_TICKS
            elif robot.state == RobotState.UNLOADING and now >= robot.busy_until:
                robot.order.delivered = now
                self.delivered.append(robot.order)
                robot.order, robot.goal, robot.state = None, None, RobotState.IDLE
            elif robot.state == RobotState.TO_PARK and robot.cell == robot.goal:
                robot.goal, robot.state = None, RobotState.IDLE

    def _send(self, robot: Robot, state: RobotState, goal: int, now: int) -> None:
        self.claims[goal] = robot.index
        robot.state, robot.goal = state, goal
        self.manager.set_goal(robot.index, robot.cell, goal, now)

    def _needed_cells(self, robot: Robot) -> tuple[int, ...]:
        if robot.state in EN_ROUTE:
            return (robot.cell, robot.goal)
        return (robot.cell,)

    def _release_claims(self) -> None:
        for cell, owner in list(self.claims.items()):
            if cell not in self._needed_cells(self.robots[owner]):
                del self.claims[cell]

    def _is_free_for(self, cell: int, robot: Robot) -> bool:
        return self.claims.get(cell, robot.index) == robot.index

    def _start_deliveries(self, now: int) -> None:
        """Loaded robots leave for their drop station when it is free, longest wait first."""
        ready = [r for r in self.robots if r.state == RobotState.LOADING and now >= r.busy_until]
        for robot in sorted(ready, key=lambda r: (r.busy_until, r.index)):
            if self._is_free_for(robot.order.drop, robot):
                self._send(robot, RobotState.TO_DROP, robot.order.drop, now)

    def _dispatch(self, now: int) -> None:
        """Oldest order first; each goes to the nearest idle robot, if its pick station is free."""
        while len(self.book) < BOOK_SIZE:
            self.book.append(self._stream.release())
        idle = [robot for robot in self.robots if robot.state == RobotState.IDLE]
        for order in list(self.book):
            if not idle:
                break
            able = [robot for robot in idle if self._is_free_for(order.pick, robot)]
            if not able:
                continue
            to_pick = self.layout.distances_to(order.pick)
            robot = min(able, key=lambda r: (to_pick[r.cell], r.index))
            idle.remove(robot)
            self.book.remove(order)
            order.robot, order.assigned = robot.index, now
            robot.order = order
            self._send(robot, RobotState.TO_PICK, order.pick, now)

    def _park_idle_robots(self, now: int) -> None:
        for robot in self.robots:
            if robot.state != RobotState.IDLE or self.layout.kinds[robot.cell] == CHARGER:
                continue
            free = [cell for cell in self.layout.chargers if cell not in self.claims]
            charger = min(free, key=lambda c: (self.layout.distances_to(c)[robot.cell], c))
            self._send(robot, RobotState.TO_PARK, charger, now)

    def _record_frame(self) -> None:
        if self.frames is not None:
            self.frames.append(
                Frame(
                    cells=tuple(robot.cell for robot in self.robots),
                    states=tuple(robot.state for robot in self.robots),
                    delivered=len(self.delivered),
                )
            )
