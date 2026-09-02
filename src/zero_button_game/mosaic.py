"""Mosaic Shift: restore an emblem with cyclic row and column shifts.

Generation starts from one of a small, quality-controlled procedural emblem
vocabulary and scrambles the tile fragments with legal moves.  The solver is
a bounded breadth-first search over the complete 3x3 permutation state space.
Accepted works have one exact shortest action sequence; candidates whose only
choice is the order of commuting, disjoint repairs are rejected as independent.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

from .core import StableRng, canonical_json_bytes, sha256_value
from .models import Action, Solution


MOSAIC_RULESET = "row-column-cyclic-shift-v1"
MOSAIC_EQUIVALENCE_POLICY = "exact ordered cyclic-shift sequence; commuting alternatives count separately"
MOSAIC_EQUIVALENCE_VERSION = "mosaic-exact-action-order-v1"
MOSAIC_GENERATOR_VERSION = "mosaic-gen-1"
BOARD_SIZE = 3
MAX_SOLVE_DEPTH = 8
MAX_SOLVE_NODES = 362_880
ART_NAMES = ("halo-diamond", "four-petal-star", "shield-knot")
AXIS_CODE = {"row": 0, "col": 1}
AXIS_NAME = {value: key for key, value in AXIS_CODE.items()}
Shift = tuple[str, int, int]


class MosaicSolveRejected(RuntimeError):
    def __init__(self, code: str, diagnostics: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.diagnostics = diagnostics or {}


@dataclass(frozen=True)
class MosaicPuzzleSpec:
    schema_version: str
    puzzle_type: str
    generator_version: str
    size: int
    initial_tiles: tuple[int, ...]
    goal_tiles: tuple[int, ...]
    art_name: str
    ruleset: str = MOSAIC_RULESET

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["initial_tiles"] = list(self.initial_tiles)
        value["goal_tiles"] = list(self.goal_tiles)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MosaicPuzzleSpec":
        return cls(
            value["schema_version"], value["puzzle_type"], value["generator_version"],
            int(value["size"]), tuple(int(item) for item in value["initial_tiles"]),
            tuple(int(item) for item in value["goal_tiles"]), value["art_name"],
            value.get("ruleset", MOSAIC_RULESET),
        )


@dataclass(frozen=True)
class MosaicState:
    puzzle_type: str
    step: int
    tiles: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"puzzle_type": self.puzzle_type, "step": self.step, "tiles": list(self.tiles)}


@dataclass(frozen=True)
class MosaicReplayStep:
    action: Action
    before: MosaicState
    after: MosaicState


@dataclass(frozen=True)
class MosaicReplayTrace:
    initial: MosaicState
    steps: tuple[MosaicReplayStep, ...]
    final: MosaicState


def action_signature(action: Action) -> Shift:
    try:
        return (
            AXIS_NAME[int(action.params["axis"][0])],
            int(action.params["line"][0]),
            int(action.params["delta"][0]),
        )
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ValueError("malformed shift_line parameters") from error


def shift_tiles(tiles: tuple[int, ...], size: int, axis: str, line: int, delta: int) -> tuple[int, ...]:
    """Return one cyclic line shift; positive is right or down."""
    if axis not in AXIS_CODE:
        raise ValueError("axis must be row or col")
    if not 0 <= line < size:
        raise ValueError("line is outside the board")
    if delta not in (-1, 1):
        raise ValueError("delta must be -1 or 1")
    shifted = list(tiles)
    if axis == "row":
        for x in range(size):
            shifted[line * size + (x + delta) % size] = tiles[line * size + x]
    else:
        for y in range(size):
            shifted[((y + delta) % size) * size + line] = tiles[y * size + line]
    return tuple(shifted)


def inverse_shift(shift: Shift) -> Shift:
    return shift[0], shift[1], -shift[2]


def shifts_interact(first: Shift, second: Shift) -> bool:
    """Different-axis lines cross and therefore do not commute."""
    return first[0] != second[0]


class MosaicRules:
    def validate_structure(self, puzzle: MosaicPuzzleSpec) -> list[str]:
        errors: list[str] = []
        expected = tuple(range(BOARD_SIZE * BOARD_SIZE))
        if puzzle.puzzle_type != "mosaic":
            errors.append("wrong puzzle type")
        if puzzle.ruleset != MOSAIC_RULESET:
            errors.append("unsupported ruleset")
        if puzzle.generator_version != MOSAIC_GENERATOR_VERSION:
            errors.append("unsupported generator version")
        if puzzle.size != BOARD_SIZE:
            errors.append("board must be 3x3")
        if tuple(sorted(puzzle.initial_tiles)) != expected:
            errors.append("initial tiles must be a permutation of 0..8")
        if puzzle.goal_tiles != expected:
            errors.append("goal tiles must be canonical row-major order")
        if puzzle.initial_tiles == puzzle.goal_tiles:
            errors.append("board is already solved")
        if puzzle.art_name not in ART_NAMES:
            errors.append("unknown emblem vocabulary entry")
        return sorted(set(errors))

    def initial_state(self, puzzle: MosaicPuzzleSpec) -> MosaicState:
        errors = self.validate_structure(puzzle)
        if errors:
            raise ValueError("; ".join(errors))
        return MosaicState("mosaic", 0, puzzle.initial_tiles)

    def is_goal(self, puzzle: MosaicPuzzleSpec, state: MosaicState) -> bool:
        return state.tiles == puzzle.goal_tiles

    def action_for(self, state: MosaicState, axis: str, line: int, delta: int) -> Action:
        if axis not in AXIS_CODE or not 0 <= line < BOARD_SIZE or delta not in (-1, 1):
            raise ValueError("illegal cyclic shift")
        return Action(
            1, "shift_line", f"{axis}-{line}",
            {"axis": [AXIS_CODE[axis]], "line": [line], "delta": [delta]},
            {"state_hash": sha256_value(state.to_dict())},
        )

    def legal_actions(self, puzzle: MosaicPuzzleSpec, state: MosaicState) -> tuple[Action, ...]:
        return tuple(
            self.action_for(state, axis, line, delta)
            for axis in ("row", "col") for line in range(puzzle.size) for delta in (-1, 1)
        )

    def apply(self, puzzle: MosaicPuzzleSpec, state: MosaicState, action: Action) -> MosaicState:
        if action.kind != "shift_line" or action.action_version != 1:
            raise ValueError("unsupported action")
        if action.precondition.get("state_hash") != sha256_value(state.to_dict()):
            raise ValueError("action precondition mismatch")
        axis, line, delta = action_signature(action)
        if action.actor_id != f"{axis}-{line}":
            raise ValueError("actor does not match shifted line")
        tiles = shift_tiles(state.tiles, puzzle.size, axis, line, delta)
        return MosaicState("mosaic", state.step + 1, tiles)


ALL_SHIFTS: tuple[Shift, ...] = tuple(
    (axis, line, delta)
    for axis in ("row", "col") for line in range(BOARD_SIZE) for delta in (-1, 1)
)


class MosaicSolver:
    """Bounded BFS with exact shortest-path counting and deterministic parent order."""

    def __init__(self, rules: MosaicRules | None = None, max_depth: int = MAX_SOLVE_DEPTH, node_budget: int = MAX_SOLVE_NODES):
        self.rules = rules or MosaicRules()
        self.max_depth = max_depth
        self.node_budget = node_budget

    def analyze(self, puzzle: MosaicPuzzleSpec) -> dict[str, Any]:
        initial = self.rules.initial_state(puzzle)
        start, goal = initial.tiles, puzzle.goal_tiles
        queue = deque([start])
        distance = {start: 0}
        ways = {start: 1}
        parent: dict[tuple[int, ...], tuple[tuple[int, ...], Shift]] = {}
        goal_depth: int | None = None
        expanded = 0
        while queue:
            tiles = queue.popleft()
            depth = distance[tiles]
            if depth >= self.max_depth or (goal_depth is not None and depth >= goal_depth):
                continue
            expanded += 1
            if expanded > self.node_budget:
                return {"status": "budget_exceeded", "depth": None, "shortest_path_count": 0, "path": (), "expanded_nodes": expanded}
            for signature in ALL_SHIFTS:
                following = shift_tiles(tiles, BOARD_SIZE, *signature)
                following_depth = depth + 1
                if following not in distance:
                    distance[following] = following_depth
                    ways[following] = ways[tiles]
                    parent[following] = (tiles, signature)
                    queue.append(following)
                elif distance[following] == following_depth:
                    ways[following] += ways[tiles]
                if following == goal and goal_depth is None:
                    goal_depth = following_depth
        path: list[Shift] = []
        if goal in distance:
            cursor = goal
            while cursor != start:
                previous, signature = parent[cursor]
                path.append(signature)
                cursor = previous
            path.reverse()
        count = ways.get(goal, 0)
        status = "unsolved" if goal not in distance else ("unique" if count == 1 else "ambiguous")
        return {"status": status, "depth": distance.get(goal), "shortest_path_count": count, "path": tuple(path), "expanded_nodes": expanded}

    def solve(self, puzzle: MosaicPuzzleSpec) -> Solution:
        analysis = self.analyze(puzzle)
        if analysis["status"] == "budget_exceeded":
            raise MosaicSolveRejected("SOLVE_BUDGET_EXCEEDED", analysis)
        if analysis["status"] == "unsolved":
            raise MosaicSolveRejected("UNSOLVABLE_WITHIN_DEPTH", analysis)
        if analysis["status"] != "unique":
            raise MosaicSolveRejected("MULTIPLE_SHORTEST_SOLUTIONS", analysis)
        state = self.rules.initial_state(puzzle)
        initial_hash = sha256_value(state.to_dict())
        actions: list[Action] = []
        for axis, line, delta in analysis["path"]:
            action = self.rules.action_for(state, axis, line, delta)
            actions.append(action)
            state = self.rules.apply(puzzle, state, action)
        signature = [list(item) for item in analysis["path"]]
        signature_hash = sha256(canonical_json_bytes(signature)).hexdigest()
        return Solution(
            "1.0.0", "mosaic-bfs", "1", "shortest-unique-exact-order", tuple(actions),
            initial_hash, sha256_value(state.to_dict()), len(actions), analysis["expanded_nodes"],
            "unique:" + signature_hash,
        )


def replay_mosaic(puzzle: MosaicPuzzleSpec, actions: tuple[Action, ...], rules: MosaicRules) -> MosaicReplayTrace:
    state = rules.initial_state(puzzle)
    initial = state
    steps: list[MosaicReplayStep] = []
    for action in actions:
        before = state
        state = rules.apply(puzzle, state, action)
        steps.append(MosaicReplayStep(action, before, state))
    return MosaicReplayTrace(initial, tuple(steps), state)


def _scramble_traits(scramble: tuple[Shift, ...]) -> dict[str, Any]:
    axes = {shift[0] for shift in scramble}
    crossings = sum(1 for first, second in zip(scramble, scramble[1:]) if shifts_interact(first, second))
    touched_lines = len({(axis, line) for axis, line, _ in scramble})
    return {
        "axis_count": len(axes), "adjacent_cross_axis_pairs": crossings,
        "touched_lines": touched_lines,
        "independent_line_repairs": crossings == 0,
    }


def generate_mosaic(rng: StableRng, preset: dict) -> MosaicPuzzleSpec:
    """Draw one deterministic scramble; outer pipeline performs solve/quality screening."""
    low = int(preset.get("min_shifts", 3))
    high = int(preset.get("max_shifts", low))
    if not 1 <= low <= high <= MAX_SOLVE_DEPTH:
        raise ValueError("MOSAIC_INVALID_SHIFT_RANGE")
    length = low + rng.randbelow(high - low + 1)
    art_name = ART_NAMES[rng.randbelow(len(ART_NAMES))]
    scramble: list[Shift] = []
    for _ in range(length):
        choices = list(ALL_SHIFTS)
        if scramble:
            inverse = inverse_shift(scramble[-1])
            choices = [item for item in choices if item != inverse]
        if len(scramble) >= 2:
            # Avoid three identical line directions, which equal a no-op on 3x3.
            choices = [
                item for item in choices
                if not (item == scramble[-1] == scramble[-2])
            ]
        scramble.append(choices[rng.randbelow(len(choices))])
    tiles = tuple(range(BOARD_SIZE * BOARD_SIZE))
    for shift in scramble:
        tiles = shift_tiles(tiles, BOARD_SIZE, *shift)
    return MosaicPuzzleSpec(
        "1.0.0", "mosaic", MOSAIC_GENERATOR_VERSION, BOARD_SIZE, tiles,
        tuple(range(BOARD_SIZE * BOARD_SIZE)), art_name,
    )


def mosaic_difficulty_preset(band: str) -> dict:
    from .preset_loader import difficulty_preset as load_difficulty_preset
    return load_difficulty_preset("mosaic", band)


def mosaic_difficulty_report(puzzle: MosaicPuzzleSpec, solution: Solution, rules: MosaicRules) -> dict[str, Any]:
    analysis = MosaicSolver(rules).analyze(puzzle)
    path = tuple(action_signature(action) for action in solution.actions)
    traits = _scramble_traits(path)
    misplaced = sum(1 for index, tile in enumerate(puzzle.initial_tiles) if index != tile)
    row_disorder = sum(
        1 for index, tile in enumerate(puzzle.initial_tiles)
        if index // BOARD_SIZE != tile // BOARD_SIZE
    )
    column_disorder = sum(
        1 for index, tile in enumerate(puzzle.initial_tiles)
        if index % BOARD_SIZE != tile % BOARD_SIZE
    )
    mechanical = {
        "shortest_actions": len(path),
        "shortest_path_count": analysis["shortest_path_count"],
        "solver_expanded_nodes": analysis["expanded_nodes"],
        "axis_count": traits["axis_count"],
        "adjacent_cross_axis_pairs": traits["adjacent_cross_axis_pairs"],
        "touched_lines": traits["touched_lines"],
        "independent_line_repairs": traits["independent_line_repairs"],
        "misplaced_tiles": misplaced,
        "row_disorder": row_disorder,
        "column_disorder": column_disorder,
    }
    mechanical["difficulty_score"] = (
        20 * len(path) + 7 * traits["adjacent_cross_axis_pairs"]
        + 3 * traits["touched_lines"] + misplaced + row_disorder + column_disorder
    )
    signature = [list(item) for item in path]
    signature_hash = sha256(canonical_json_bytes(signature)).hexdigest()
    return {
        "mechanical": mechanical,
        "human": {
            "status": "uncalibrated-mosaic-v1", "model_version": None,
            "calibration_scope": None, "predicted_correct_time_ms": None,
            "p_solve_before_reveal": None,
            "features": {
                "shortest_actions": len(path), "misplaced_tiles": misplaced,
                "cross_axis_pairs": traits["adjacent_cross_axis_pairs"],
            },
        },
        "solution_uniqueness": {
            "status": analysis["status"],
            "shortest_depth": analysis["depth"],
            "shortest_path_count": analysis["shortest_path_count"],
            "proof": "bounded-complete-bfs-through-depth-8",
            "expanded_nodes": analysis["expanded_nodes"],
            "equivalence_policy": MOSAIC_EQUIVALENCE_POLICY,
            "equivalence_policy_version": MOSAIC_EQUIVALENCE_VERSION,
            "normalized_signature": signature,
            "normalized_signature_hash": signature_hash,
        },
        "art_quality": {
            "vocabulary": "quality-controlled-procedural-emblems-v1",
            "emblem": puzzle.art_name,
            "fragment_count": BOARD_SIZE * BOARD_SIZE,
            "minimum_stroke_px": 12,
            "shape_redundancy": "outline plus ring/diamond/petal geometry; not colour-only",
            "initially_solved": puzzle.initial_tiles == puzzle.goal_tiles,
        },
        "requested_band": None, "accepted_band": None, "quality_preset": None,
    }


def mosaic_quality_rejection(difficulty: dict[str, Any], requested_band: str = "medium") -> str | None:
    preset = mosaic_difficulty_preset(requested_band)
    metrics = difficulty["mechanical"]
    uniqueness = difficulty["solution_uniqueness"]
    difficulty["requested_band"] = requested_band
    difficulty["quality_preset"] = preset["name"]
    if uniqueness["status"] != "unique" or uniqueness["shortest_path_count"] != 1:
        return "MULTIPLE_SHORTEST_SOLUTIONS"
    if metrics["shortest_actions"] < preset["min_shifts"]:
        return "TOO_TRIVIAL"
    if metrics["shortest_actions"] > preset["max_shifts"]:
        return "OUTSIDE_BAND"
    if metrics["axis_count"] < 2:
        return "SINGLE_AXIS_REPAIR"
    if metrics["independent_line_repairs"] or metrics["adjacent_cross_axis_pairs"] < preset["min_cross_axis_pairs"]:
        return "INDEPENDENT_LINE_REPAIRS"
    if metrics["misplaced_tiles"] < preset["min_misplaced_tiles"]:
        return "EMBLEM_TOO_EASY_TO_INFER"
    if metrics["difficulty_score"] < preset["min_difficulty_score"]:
        return "TOO_TRIVIAL"
    if metrics["difficulty_score"] > preset["max_difficulty_score"]:
        return "OUTSIDE_BAND"
    difficulty["accepted_band"] = requested_band
    difficulty["human"]["status"] = "uncalibrated-mosaic-v1"
    return None


def validate_mosaic_solution(puzzle: MosaicPuzzleSpec, solution: Solution, rules: MosaicRules) -> list[str]:
    errors = list(rules.validate_structure(puzzle))
    try:
        trace = replay_mosaic(puzzle, solution.actions, rules)
    except ValueError as error:
        return sorted(set(errors + [str(error)]))
    if solution.initial_state_hash != sha256_value(trace.initial.to_dict()):
        errors.append("initial state hash mismatch")
    if solution.final_state_hash != sha256_value(trace.final.to_dict()):
        errors.append("final state hash mismatch")
    if not rules.is_goal(puzzle, trace.final):
        errors.append("solution does not restore the emblem")
    analysis = MosaicSolver(rules).analyze(puzzle)
    if analysis["status"] != "unique" or analysis["shortest_path_count"] != 1:
        errors.append("solution is not the unique exact shortest sequence")
    if analysis["depth"] != len(solution.actions) or solution.cost != len(solution.actions):
        errors.append("solution action count is not shortest")
    if tuple(action_signature(action) for action in solution.actions) != analysis["path"]:
        errors.append("solution differs from canonical shortest sequence")
    return sorted(set(errors))
