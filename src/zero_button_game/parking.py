"""Parking-lot escape (Rush Hour style) puzzle logic.

The board is a 6x6 grid holding axis-aligned vehicles. Every vehicle slides
only along its own axis and never passes through another vehicle. The target
vehicle is horizontal and must leave the board through the fixed EAST exit on
its own row.

Uniqueness policy: a candidate is accepted only when the normalized minimal
move sequence is unique. "Normalized" folds consecutive slides of the same
vehicle into one slide; on a minimal path such a fold can never apply, so the
normalization is an identity check that also guarantees the emitted sequence is
already canonical.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

from .core import StableRng, canonical_json_bytes, sha256_value
from .models import Action, Solution

Cell = tuple[int, int]
HORIZONTAL = "H"
VERTICAL = "V"
PARKING_RULESET = "rush-hour-slide-v1"
PARKING_EQUIVALENCE_POLICY = "normalized minimal move sequence (vehicle_id, signed slide_cells)"
PARKING_EQUIVALENCE_VERSION = "parking-normalized-minimum-v1"
SOLVE_STATE_BUDGET = 400_000

Vehicle = tuple[int, int, int, str, int]


class ParkingSolveRejected(RuntimeError):
    def __init__(self, code: str, diagnostics: dict | None = None):
        super().__init__(code)
        self.code = code
        self.diagnostics = diagnostics or {}


@dataclass(frozen=True)
class ParkingPuzzleSpec:
    schema_version: str
    puzzle_type: str
    generator_version: str
    width: int
    height: int
    vehicles: tuple[Vehicle, ...]
    target_id: int
    exit_side: str = "east"
    ruleset: str = PARKING_RULESET

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["vehicles"] = [list(item) for item in self.vehicles]
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ParkingPuzzleSpec":
        return cls(
            value["schema_version"], value["puzzle_type"], value["generator_version"],
            value["width"], value["height"],
            tuple((int(x), int(y), int(length), str(orientation), int(vehicle_id)) for x, y, length, orientation, vehicle_id in value["vehicles"]),
            int(value["target_id"]), value.get("exit_side", "east"), value.get("ruleset", PARKING_RULESET),
        )

    def index_of(self, vehicle_id: int) -> int:
        for index, vehicle in enumerate(self.vehicles):
            if vehicle[4] == vehicle_id:
                return index
        raise ValueError(f"unknown vehicle id: {vehicle_id}")


@dataclass(frozen=True)
class ParkingState:
    puzzle_type: str
    step: int
    positions: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParkingReplayStep:
    action: Action
    before: ParkingState
    after: ParkingState


@dataclass(frozen=True)
class ParkingReplayTrace:
    initial: ParkingState
    steps: tuple[ParkingReplayStep, ...]
    final: ParkingState


def vehicle_cells(puzzle: ParkingPuzzleSpec, index: int, offset: int) -> tuple[Cell, ...]:
    x, y, length, orientation, _ = puzzle.vehicles[index]
    if orientation == HORIZONTAL:
        return tuple((x + offset + step, y) for step in range(length))
    return tuple((x, y + offset + step) for step in range(length))


def board_occupancy(puzzle: ParkingPuzzleSpec, positions: tuple[int, ...]) -> dict[Cell, int]:
    grid: dict[Cell, int] = {}
    for index in range(len(puzzle.vehicles)):
        for cell in vehicle_cells(puzzle, index, positions[index]):
            if 0 <= cell[0] < puzzle.width and 0 <= cell[1] < puzzle.height:
                grid[cell] = index
    return grid


def _target_exit_offset(puzzle: ParkingPuzzleSpec) -> int:
    x, _, _, _, _ = puzzle.vehicles[puzzle.index_of(puzzle.target_id)]
    return puzzle.width - x


def is_solved(puzzle: ParkingPuzzleSpec, positions: tuple[int, ...]) -> bool:
    return positions[puzzle.index_of(puzzle.target_id)] == _target_exit_offset(puzzle)


def slide_moves(
    puzzle: ParkingPuzzleSpec, positions: tuple[int, ...], allow_solved: bool = False,
) -> list[tuple[int, int, tuple[int, ...]]]:
    """All legal slides as (vehicle index, signed slide cells, resulting positions).

    Solved states are terminal for search; ``allow_solved`` is used only to walk
    the (symmetric) move graph backwards during path reconstruction.
    """
    if is_solved(puzzle, positions) and not allow_solved:
        return []
    grid = board_occupancy(puzzle, positions)
    target_index = puzzle.index_of(puzzle.target_id)
    moves: list[tuple[int, int, tuple[int, ...]]] = []
    for index, (x, y, length, orientation, _) in enumerate(puzzle.vehicles):
        offset = positions[index]
        axis_min = -(x if orientation == HORIZONTAL else y)
        limit = (puzzle.width if orientation == HORIZONTAL else puzzle.height) - length
        axis_max = limit - (x if orientation == HORIZONTAL else y)
        head = offset + length - 1
        # forward (east / south)
        step = 1
        while offset + step <= axis_max:
            cell = (x + head + step, y) if orientation == HORIZONTAL else (x, y + head + step)
            if cell in grid:
                break
            moves.append((index, step, _with_offset(positions, index, offset + step)))
            step += 1
        if index == target_index and offset + step > axis_max:
            # The corridor to the east rim is clear: one slide takes the car out.
            exit_offset = _target_exit_offset(puzzle)
            if exit_offset > offset:
                moves.append((index, exit_offset - offset, _with_offset(positions, index, exit_offset)))
        # backward (west / north)
        step = 1
        while offset - step >= axis_min:
            cell = (x + offset - step, y) if orientation == HORIZONTAL else (x, y + offset - step)
            if cell in grid:
                break
            moves.append((index, -step, _with_offset(positions, index, offset - step)))
            step += 1
    return moves


def _with_offset(positions: tuple[int, ...], index: int, value: int) -> tuple[int, ...]:
    updated = list(positions)
    updated[index] = value
    return tuple(updated)


def make_action(puzzle: ParkingPuzzleSpec, state: ParkingState, index: int, delta: int) -> Action:
    vehicle = puzzle.vehicles[index]
    axis = 0 if vehicle[3] == HORIZONTAL else 1
    return Action(
        1, "move_piece", f"vehicle-{vehicle[4]}",
        {"vehicle": [vehicle[4]], "delta": [delta], "slide_cells": [abs(delta)], "axis": [axis]},
        {"state_hash": sha256_value(state.to_dict())},
    )


class ParkingRules:
    def validate_structure(self, puzzle: ParkingPuzzleSpec) -> list[str]:
        errors: list[str] = []
        if puzzle.puzzle_type != "parking":
            errors.append("wrong puzzle type")
        if puzzle.ruleset != PARKING_RULESET:
            errors.append("unsupported ruleset")
        if puzzle.exit_side != "east":
            errors.append("only the east exit is supported")
        if not (4 <= puzzle.width <= 8 and 4 <= puzzle.height <= 8):
            errors.append("dimensions outside 4..8")
        if not puzzle.vehicles:
            errors.append("no vehicles")
            return errors
        ids = [vehicle[4] for vehicle in puzzle.vehicles]
        if len(set(ids)) != len(ids):
            errors.append("duplicate vehicle id")
        if puzzle.target_id not in ids:
            errors.append("target vehicle missing")
            return errors
        occupied: dict[Cell, int] = {}
        for index, (x, y, length, orientation, _) in enumerate(puzzle.vehicles):
            if orientation not in (HORIZONTAL, VERTICAL):
                errors.append("invalid orientation")
                continue
            if not 2 <= length <= 3:
                errors.append("vehicle length outside 2..3")
                continue
            for cell in vehicle_cells(puzzle, index, 0):
                if not (0 <= cell[0] < puzzle.width and 0 <= cell[1] < puzzle.height):
                    errors.append("vehicle outside the board")
                elif cell in occupied:
                    errors.append("overlapping vehicles")
                else:
                    occupied[cell] = index
        target = puzzle.vehicles[puzzle.index_of(puzzle.target_id)] if puzzle.target_id in ids else None
        if target is not None and target[3] != HORIZONTAL:
            errors.append("target vehicle must be horizontal")
        return sorted(set(errors))

    def initial_state(self, puzzle: ParkingPuzzleSpec) -> ParkingState:
        return ParkingState("parking", 0, (0,) * len(puzzle.vehicles))

    def legal_actions(self, puzzle: ParkingPuzzleSpec, state: ParkingState) -> tuple[Action, ...]:
        return tuple(
            make_action(puzzle, state, index, delta)
            for index, delta, _ in slide_moves(puzzle, state.positions)
        )

    def apply(self, puzzle: ParkingPuzzleSpec, state: ParkingState, action: Action) -> ParkingState:
        if action.kind != "move_piece" or not action.actor_id.startswith("vehicle-"):
            raise ValueError("unsupported action")
        if action.precondition.get("state_hash") != sha256_value(state.to_dict()):
            raise ValueError("action precondition mismatch")
        vehicle_value = action.params.get("vehicle", [])
        delta_value = action.params.get("delta", [])
        slide_value = action.params.get("slide_cells", [])
        axis_value = action.params.get("axis", [])
        if len(vehicle_value) != 1 or len(delta_value) != 1 or len(slide_value) != 1 or len(axis_value) != 1:
            raise ValueError("invalid move_piece parameters")
        vehicle_id, delta = vehicle_value[0], delta_value[0]
        try:
            index = puzzle.index_of(vehicle_id)
        except ValueError as error:
            raise ValueError("illegal vehicle slide") from error
        if action.actor_id != f"vehicle-{vehicle_id}":
            raise ValueError("vehicle actor/id mismatch")
        if slide_value[0] != abs(delta) or delta == 0:
            raise ValueError("invalid move_piece parameters")
        if axis_value[0] != (0 if puzzle.vehicles[index][3] == HORIZONTAL else 1):
            raise ValueError("illegal vehicle slide: wrong axis")
        for candidate_index, candidate_delta, positions in slide_moves(puzzle, state.positions):
            if candidate_index == index and candidate_delta == delta:
                return ParkingState("parking", state.step + 1, positions)
        raise ValueError("illegal vehicle slide")

    def is_goal(self, puzzle: ParkingPuzzleSpec, state: ParkingState) -> bool:
        return is_solved(puzzle, state.positions)

    def exit_row(self, puzzle: ParkingPuzzleSpec) -> int:
        return puzzle.vehicles[puzzle.index_of(puzzle.target_id)][1]


def replay_parking(puzzle: ParkingPuzzleSpec, actions: tuple[Action, ...], rules: ParkingRules) -> ParkingReplayTrace:
    state = rules.initial_state(puzzle)
    initial = state
    steps = []
    for action in actions:
        before = state
        state = rules.apply(puzzle, state, action)
        steps.append(ParkingReplayStep(action, before, state))
    return ParkingReplayTrace(initial, tuple(steps), state)


def normalize_moves(moves: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    """Fold consecutive slides of the same vehicle into a single slide."""
    folded: list[list[int]] = []
    for vehicle_id, delta in moves:
        if folded and folded[-1][0] == vehicle_id:
            folded[-1][1] += delta
        else:
            folded.append([vehicle_id, delta])
    return tuple((vehicle_id, delta) for vehicle_id, delta in folded if delta != 0)


def normalized_signature_hash(moves: tuple[tuple[int, int], ...]) -> str:
    return sha256(canonical_json_bytes([[vehicle_id, delta] for vehicle_id, delta in moves])).hexdigest()


class ParkingSolver:
    solver_id = "parking-bfs-unique"
    solver_version = "1"

    def __init__(self, rules: ParkingRules):
        self.rules = rules

    def analyze(self, puzzle: ParkingPuzzleSpec, state_budget: int = SOLVE_STATE_BUDGET) -> dict:
        """Full BFS distance map plus a shortest-path count over the parent DAG."""
        start = self.rules.initial_state(puzzle).positions
        distance = {start: 0}
        counts = {start: 1}
        queue = deque([start])
        goal_states: list[tuple[int, ...]] = []
        expanded = 0
        while queue:
            current = queue.popleft()
            expanded += 1
            if is_solved(puzzle, current):
                goal_states.append(current)
                continue
            for _, _, nxt in slide_moves(puzzle, current):
                if nxt not in distance:
                    if len(distance) >= state_budget:
                        raise ParkingSolveRejected("SOLVE_BUDGET_EXCEEDED", {"reachable_states": len(distance)})
                    distance[nxt] = distance[current] + 1
                    counts[nxt] = counts[current]
                    queue.append(nxt)
                elif distance[nxt] == distance[current] + 1:
                    counts[nxt] = min(1_000_000, counts[nxt] + counts[current])
        if not goal_states:
            return {
                "status": "unsolvable", "reachable_states": len(distance), "expanded_nodes": expanded,
                "minimum_moves": None, "minimal_path_count": 0, "goal_states": 0,
            }
        best = min(distance[state] for state in goal_states)
        winners = [state for state in goal_states if distance[state] == best]
        total = sum(counts[state] for state in winners)
        return {
            "status": "unique" if total == 1 else "ambiguous",
            "reachable_states": len(distance), "expanded_nodes": expanded,
            "minimum_moves": best, "minimal_path_count": total,
            "goal_states": len(goal_states), "minimal_goal_states": len(winners),
            "_distance": distance, "_goal": winners[0] if total == 1 else None,
        }

    def solve(self, puzzle: ParkingPuzzleSpec, state_budget: int = SOLVE_STATE_BUDGET) -> Solution:
        analysis = self.analyze(puzzle, state_budget)
        public = {key: value for key, value in analysis.items() if not key.startswith("_")}
        if analysis["status"] == "unsolvable":
            raise ParkingSolveRejected("UNSOLVABLE", public)
        if analysis["status"] != "unique":
            raise ParkingSolveRejected("MULTIPLE_MINIMAL_PATHS", public)
        distance = analysis["_distance"]
        chain = [analysis["_goal"]]
        deltas: list[tuple[int, int]] = []
        current = analysis["_goal"]
        start = self.rules.initial_state(puzzle).positions
        while current != start:
            predecessor = None
            for index, delta, nxt in slide_moves(puzzle, current, allow_solved=True):
                if distance.get(nxt) == distance[current] - 1:
                    predecessor = (nxt, puzzle.vehicles[index][4], -delta)
                    break
            if predecessor is None:
                raise ParkingSolveRejected("SOLVER_INTERNAL_ERROR", public)
            current = predecessor[0]
            chain.append(current)
            deltas.append((predecessor[1], predecessor[2]))
        deltas.reverse()
        normalized = normalize_moves(tuple(deltas))
        if normalized != tuple(deltas):
            raise ParkingSolveRejected("SOLVER_INTERNAL_ERROR", public)
        state = self.rules.initial_state(puzzle)
        initial_hash = sha256_value(state.to_dict())
        actions = []
        for vehicle_id, delta in deltas:
            action = make_action(puzzle, state, puzzle.index_of(vehicle_id), delta)
            state = self.rules.apply(puzzle, state, action)
            actions.append(action)
        if not self.rules.is_goal(puzzle, state):
            raise ParkingSolveRejected("SOLVER_INTERNAL_ERROR", public)
        return Solution(
            "1.0.0", self.solver_id, self.solver_version, "proven_unique_minimum_moves",
            tuple(actions), initial_hash, sha256_value(state.to_dict()),
            len(actions), analysis["expanded_nodes"],
            "unique:" + normalized_signature_hash(normalized),
        )


def validate_parking_solution(puzzle: ParkingPuzzleSpec, solution: Solution, rules: ParkingRules) -> list[str]:
    failures = rules.validate_structure(puzzle)
    if failures:
        return failures
    try:
        trace = replay_parking(puzzle, solution.actions, rules)
    except ValueError as error:
        failures.append(f"illegal action: {error}")
        return failures
    if not rules.is_goal(puzzle, trace.final):
        failures.append("target vehicle does not leave through the east exit")
    if sha256_value(trace.initial.to_dict()) != solution.initial_state_hash:
        failures.append("initial state hash mismatch")
    if sha256_value(trace.final.to_dict()) != solution.final_state_hash:
        failures.append("final state hash mismatch")
    if solution.cost != len(solution.actions):
        failures.append("solution cost mismatch")
    moves = tuple((action.params["vehicle"][0], action.params["delta"][0]) for action in solution.actions)
    normalized = normalize_moves(moves)
    if normalized != moves:
        failures.append("solution contains foldable consecutive slides")
    analysis = ParkingSolver(rules).analyze(puzzle)
    if analysis["status"] != "unique":
        failures.append("minimal move sequence is not unique")
    elif analysis["minimum_moves"] != len(solution.actions):
        failures.append("solution does not match the minimum-move oracle")
    elif solution.answer_equivalence_key != "unique:" + normalized_signature_hash(normalized):
        failures.append("answer equivalence key mismatch")
    return failures


# --------------------------------------------------------------------------
# Generation (reverse walk from a cleared state)
# --------------------------------------------------------------------------


def _spec_from_positions(puzzle: ParkingPuzzleSpec, positions: tuple[int, ...]) -> ParkingPuzzleSpec:
    vehicles = []
    for index, (x, y, length, orientation, vehicle_id) in enumerate(puzzle.vehicles):
        offset = positions[index]
        if orientation == HORIZONTAL:
            vehicles.append((x + offset, y, length, orientation, vehicle_id))
        else:
            vehicles.append((x, y + offset, length, orientation, vehicle_id))
    return ParkingPuzzleSpec(
        puzzle.schema_version, puzzle.puzzle_type, puzzle.generator_version,
        puzzle.width, puzzle.height, tuple(vehicles), puzzle.target_id,
        puzzle.exit_side, puzzle.ruleset,
    )


def _random_layout(rng: StableRng, preset: dict) -> tuple[ParkingPuzzleSpec, int]:
    """A cleared board: the target is out and no vehicle stands on the exit row."""
    width = int(preset.get("width", 6))
    height = int(preset.get("height", 6))
    vehicle_count = int(preset.get("vehicle_count", 12))
    blocker_count = int(preset.get("blocker_count", 2))
    exit_row = rng.randbelow(height)
    vehicles: list[Vehicle] = [(0, exit_row, 2, HORIZONTAL, 0)]
    occupied = {(x, exit_row) for x in range(width)}
    # Prospective blockers park immediately above or below the corridor so the
    # reverse walk always has a way to close it again.
    columns = list(range(2, width))
    rng.shuffle(columns)
    placed = 0
    for column in columns:
        if placed >= blocker_count:
            break
        length = 2 if rng.randbelow(3) else 3
        options = []
        if exit_row - length >= 0:
            options.append(exit_row - length)
        if exit_row + 1 + length <= height:
            options.append(exit_row + 1)
        if not options:
            continue
        y = options[rng.randbelow(len(options))]
        cells = [(column, y + step) for step in range(length)]
        if any(cell in occupied for cell in cells):
            continue
        occupied.update(cells)
        vehicles.append((column, y, length, VERTICAL, len(vehicles)))
        placed += 1
    attempts = 0
    while len(vehicles) < vehicle_count + 1 and attempts < 300:
        attempts += 1
        length = 2 if rng.randbelow(4) else 3
        orientation = HORIZONTAL if rng.randbelow(2) else VERTICAL
        if orientation == HORIZONTAL:
            x = rng.randbelow(max(1, width - length + 1))
            y = rng.randbelow(height)
            cells = [(x + step, y) for step in range(length)]
        else:
            x = rng.randbelow(width)
            y = rng.randbelow(max(1, height - length + 1))
            cells = [(x, y + step) for step in range(length)]
        if any(cell in occupied for cell in cells):
            continue
        occupied.update(cells)
        vehicles.append((cells[0][0], cells[0][1], length, orientation, len(vehicles)))
    layout = ParkingPuzzleSpec(
        "1.0.0", "parking", "parking-gen-1", width, height, tuple(vehicles), 0, "east", PARKING_RULESET,
    )
    return layout, exit_row


def _reverse_walk(rng: StableRng, layout: ParkingPuzzleSpec, exit_row: int, walk_steps: int) -> tuple[int, ...]:
    """Walk backwards from the cleared board along legal (hence reversible) slides.

    The target is placed at the far west end of its row first; every later step
    moves a blocker and must leave the corridor obstructed, so the walked state
    is always reachable from the goal and never already solved.
    """
    positions = (0,) * len(layout.vehicles)

    def corridor_blocked(state: tuple[int, ...]) -> bool:
        grid = board_occupancy(layout, state)
        return any((x, exit_row) in grid for x in range(2, layout.width))

    seen = {positions}
    previous = -1
    for _ in range(walk_steps):
        options = [
            item for item in slide_moves(layout, positions, allow_solved=True)
            if item[0] != 0 and item[0] != previous and item[2] not in seen and corridor_blocked(item[2])
        ]
        if not options:
            options = [
                item for item in slide_moves(layout, positions, allow_solved=True)
                if item[0] != 0 and corridor_blocked(item[2])
            ]
        if not options:
            break
        index, _, positions = options[rng.randbelow(len(options))]
        previous = index
        seen.add(positions)
    return positions


def draw_candidate(rng: StableRng, preset: dict) -> ParkingPuzzleSpec:
    """One unscreened reverse-generated board.

    This is what the uniqueness screen inside :func:`generate_parking` draws
    from; it is exported so tests can exercise the rejection paths too.
    """
    jittered = dict(preset)
    jittered["vehicle_count"] = int(preset.get("vehicle_count", 12)) + rng.randbelow(3) - 1
    walk_steps = int(preset.get("walk_steps", 16)) + rng.randbelow(6)
    layout, exit_row = _random_layout(rng, jittered)
    return _spec_from_positions(layout, _reverse_walk(rng, layout, exit_row, walk_steps))


def generate_parking(rng: StableRng, preset: dict) -> ParkingPuzzleSpec:
    """Reverse generation with an in-generator uniqueness screen.

    Random forward layouts almost never have a unique normalized minimal move
    sequence, so the generator walks backwards from the cleared board and keeps
    drawing candidates until one passes its own screen. The pipeline re-solves
    and re-filters whatever comes out; nothing here is trusted as an answer.
    """
    rules = ParkingRules()
    solver = ParkingSolver(rules)
    budget = int(preset.get("solve_state_budget", 60_000))
    attempts = int(preset.get("search_attempts", 120))
    low = max(4, int(preset.get("min_moves", 4)))
    high = int(preset.get("max_moves", 6))
    fallback: ParkingPuzzleSpec | None = None
    for _ in range(max(1, attempts)):
        candidate = draw_candidate(rng, preset)
        if rules.validate_structure(candidate):
            continue
        if fallback is None:
            fallback = candidate
        # Cheap screen first: one BFS decides uniqueness and the move count.
        try:
            analysis = solver.analyze(candidate, budget)
        except ParkingSolveRejected:
            continue
        if analysis["status"] != "unique":
            continue
        if not low <= analysis["minimum_moves"] <= high:
            continue
        solution = solver.solve(candidate, budget)
        report = parking_difficulty_report(candidate, solution, rules)
        if parking_quality_rejection(report, preset.get("band", "medium")) is None:
            return candidate
    if fallback is None:
        raise ValueError("PARKING_LAYOUT_FAILED: no structurally valid layout was produced")
    return fallback


# --------------------------------------------------------------------------
# Difficulty
# --------------------------------------------------------------------------


def _clearance_blockers(puzzle: ParkingPuzzleSpec, grid: dict[Cell, int], index: int, forbidden: set[Cell]) -> set[int] | None:
    """Vehicles that stand in the way of moving `index` out of `forbidden`."""
    x, y, length, orientation, _ = puzzle.vehicles[index]
    cells = list(vehicle_cells(puzzle, index, 0))
    best: set[int] | None = None
    for direction in (1, -1):
        blockers: set[int] = set()
        shifted = cells
        feasible = True
        for _ in range(puzzle.width + puzzle.height):
            if not any(cell in forbidden for cell in shifted):
                break
            shifted = [(cx + direction, cy) if orientation == HORIZONTAL else (cx, cy + direction) for cx, cy in shifted]
            if any(not (0 <= cx < puzzle.width and 0 <= cy < puzzle.height) for cx, cy in shifted):
                feasible = False
                break
            for cell in shifted:
                other = grid.get(cell)
                if other is not None and other != index:
                    blockers.add(other)
        else:
            feasible = False
        if feasible and (best is None or len(blockers) < len(best)):
            best = blockers
    return best


def blocking_chain_depth(puzzle: ParkingPuzzleSpec, rules: ParkingRules) -> int:
    grid = board_occupancy(puzzle, rules.initial_state(puzzle).positions)
    target_index = puzzle.index_of(puzzle.target_id)
    tx, ty, tlength, _, _ = puzzle.vehicles[target_index]
    corridor = {(x, ty) for x in range(tx + tlength, puzzle.width)}
    frontier = [(index, corridor) for cell, index in grid.items() if cell in corridor]
    seen = {target_index}
    depth = 0
    while frontier:
        nxt: list[tuple[int, set[Cell]]] = []
        advanced = False
        for index, forbidden in frontier:
            if index in seen:
                continue
            advanced = True
            seen.add(index)
            blockers = _clearance_blockers(puzzle, grid, index, forbidden)
            for other in blockers or ():
                if other not in seen:
                    nxt.append((other, set(vehicle_cells(puzzle, index, 0))))
        if not advanced:
            break
        # A level counts only when it actually introduced a new vehicle.
        depth += 1
        frontier = nxt
    return depth


def parking_difficulty_preset(band: str) -> dict:
    from .preset_loader import difficulty_preset as load_difficulty_preset
    return load_difficulty_preset("parking", band)


def parking_difficulty_report(puzzle: ParkingPuzzleSpec, solution: Solution, rules: ParkingRules) -> dict:
    moves = tuple((action.params["vehicle"][0], action.params["delta"][0]) for action in solution.actions)
    normalized = normalize_moves(moves)
    involved = {vehicle_id for vehicle_id, _ in normalized}
    target_moves = [delta for vehicle_id, delta in normalized if vehicle_id == puzzle.target_id]
    reversal_moves = sum(1 for delta in target_moves if delta < 0)
    initial = rules.initial_state(puzzle)
    mobile = {index for index, _, _ in slide_moves(puzzle, initial.positions)}
    noise = len({puzzle.vehicles[index][4] for index in mobile} - involved)
    depth = blocking_chain_depth(puzzle, rules)
    grid = board_occupancy(puzzle, initial.positions)
    target_index = puzzle.index_of(puzzle.target_id)
    tx, ty, tlength, _, _ = puzzle.vehicles[target_index]
    direct_blockers = len({grid[(x, ty)] for x in range(tx + tlength, puzzle.width) if (x, ty) in grid})
    analysis = ParkingSolver(rules).analyze(puzzle)
    mechanical = {
        "normalized_moves": len(normalized),
        "involved_vehicles": len(involved),
        "blocking_chain_depth": depth,
        "direct_blockers": direct_blockers,
        "noise_vehicles": noise,
        "reversal_moves": reversal_moves,
        "slide_cells_total": sum(abs(delta) for _, delta in normalized),
        "vehicle_count": len(puzzle.vehicles),
        "mobile_vehicles": len(mobile),
        "occupied_cells": len(grid),
        "board_cells": puzzle.width * puzzle.height,
        "reachable_states": analysis["reachable_states"],
        "solver_expanded_nodes": solution.expanded_nodes,
    }
    # Noise vehicles are reported but deliberately kept out of the score: they
    # vary a lot between boards and would smear the band boundaries.
    mechanical["difficulty_score"] = (
        6 * mechanical["normalized_moves"]
        + 3 * mechanical["involved_vehicles"]
        + 5 * mechanical["blocking_chain_depth"]
        + 4 * mechanical["reversal_moves"]
        + mechanical["slide_cells_total"]
        + mechanical["board_cells"] // 4
    )
    return {
        "mechanical": mechanical,
        "human": {
            "status": "uncalibrated-parking-v1", "model_version": None,
            "calibration_scope": None, "predicted_correct_time_ms": None,
            "p_solve_before_reveal": None,
            "features": {
                "normalized_moves": mechanical["normalized_moves"],
                "involved_vehicles": mechanical["involved_vehicles"],
                "blocking_chain_depth": mechanical["blocking_chain_depth"],
                "reversal_moves": mechanical["reversal_moves"],
                "noise_vehicles": mechanical["noise_vehicles"],
            },
        },
        "solution_uniqueness": {
            "status": analysis["status"],
            "minimal_path_count": analysis["minimal_path_count"],
            "minimum_moves": analysis["minimum_moves"],
            "reachable_states": analysis["reachable_states"],
            "equivalence_policy": PARKING_EQUIVALENCE_POLICY,
            "equivalence_policy_version": PARKING_EQUIVALENCE_VERSION,
            "normalized_signature": [[vehicle_id, delta] for vehicle_id, delta in normalized],
            "normalized_signature_hash": normalized_signature_hash(normalized),
        },
        "requested_band": None, "accepted_band": None, "quality_preset": None,
    }


def parking_quality_rejection(difficulty: dict, requested_band: str = "medium") -> str | None:
    preset = parking_difficulty_preset(requested_band)
    metrics = difficulty["mechanical"]
    difficulty["requested_band"] = requested_band
    difficulty["quality_preset"] = preset["name"]
    uniqueness = difficulty.get("solution_uniqueness")
    if uniqueness is not None and uniqueness["minimal_path_count"] != 1:
        return "MULTIPLE_MINIMAL_PATHS"
    # A cyclic shift of the move list is the neutrality perturbation, so fewer
    # than four normalized moves is rejected in every band.
    if metrics["normalized_moves"] < max(4, preset["min_moves"]):
        return "MOVE_COUNT_TOO_LOW"
    if metrics["normalized_moves"] > preset["max_moves"]:
        return "ANIMATION_TOO_LONG"
    if metrics["slide_cells_total"] > preset["max_slide_cells"]:
        return "ANIMATION_TOO_LONG"
    if metrics["involved_vehicles"] < preset["min_involved_vehicles"]:
        return "TOO_TRIVIAL"
    if metrics["blocking_chain_depth"] < preset["min_blocking_chain_depth"]:
        return "SHALLOW_BLOCKING_CHAIN"
    if metrics["reversal_moves"] < preset["min_reversal_moves"]:
        return "NO_REVERSAL_STEP"
    if metrics["difficulty_score"] < preset["min_difficulty_score"]:
        return "TOO_TRIVIAL"
    if metrics["difficulty_score"] > preset["max_difficulty_score"]:
        return "OUTSIDE_BAND"
    difficulty["accepted_band"] = requested_band
    difficulty["human"]["status"] = "uncalibrated-parking-v1"
    return None
