"""Lights Out (toggle-plus, GF(2) proof) puzzle logic.

Every cell of a small grid is either lit or unlit. Pressing a cell toggles that
cell and its four orthogonal neighbours - a plus of at most five cells. The
goal is the all-lit board.

Uniqueness policy: presses commute and pressing twice is a no-op, so a solution
is a *set* of cells, and the puzzle is the GF(2) linear system ``A s = b`` where
column ``A_i`` is the toggle mask of cell ``i`` and ``b`` is ``initial XOR
all-on``. Forward elimination yields the rank; ``nullity == 0`` proves the
solution both exists and is unique in one step - a proof, not a search. Any
board whose grid shape has ``nullity > 0`` is rejected outright, which is why
the 5x4 board is used: 4x4 and 5x5 have nullity 4 and 2 respectively and can
never be made unique.

Generation therefore runs backwards: press ``k`` distinct cells starting from
the all-lit board. Because ``A`` is invertible for this shape, that press set
*is* the unique solution; nothing has to be searched for.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any, Iterable

from .core import StableRng, canonical_json_bytes, sha256_value
from .models import Action, Solution

Cell = tuple[int, int]

LIGHTS_RULESET = "toggle-plus-gf2-v1"
LIGHTS_EQUIVALENCE_POLICY = "set of pressed cells over the unique GF(2) solution of A s = initial XOR all-on"
LIGHTS_EQUIVALENCE_VERSION = "lights-press-set-gf2-v1"

# The board shape is structural, not cosmetic: only shapes whose toggle matrix
# has full column rank can ever produce a provably unique press set.
BOARD_WIDTH = 5
BOARD_HEIGHT = 4
MIN_PRESS_COUNT = 2  # the neutrality perturbation is a cyclic shift of the press order


class LightsSolveRejected(RuntimeError):
    def __init__(self, code: str, diagnostics: dict | None = None):
        super().__init__(code)
        self.code = code
        self.diagnostics = diagnostics or {}


def cell_index(width: int, cell: Cell) -> int:
    return cell[1] * width + cell[0]


def plus_cells(width: int, height: int, cell: Cell) -> tuple[Cell, ...]:
    """The cell itself plus its in-bounds orthogonal neighbours."""
    x, y = cell
    found = []
    for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = x + dx, y + dy
        if 0 <= nx < width and 0 <= ny < height:
            found.append((nx, ny))
    return tuple(sorted(found))


def press_masks(width: int, height: int) -> tuple[int, ...]:
    """Column ``A_i``: the bitmask of lights flipped by pressing cell ``i``."""
    masks = []
    for index in range(width * height):
        cell = (index % width, index // width)
        mask = 0
        for neighbour in plus_cells(width, height, cell):
            mask |= 1 << cell_index(width, neighbour)
        masks.append(mask)
    return tuple(masks)


@dataclass(frozen=True)
class LightsPuzzleSpec:
    schema_version: str
    puzzle_type: str
    generator_version: str
    width: int
    height: int
    initial: tuple[int, ...]
    ruleset: str = LIGHTS_RULESET

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["initial"] = list(self.initial)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "LightsPuzzleSpec":
        return cls(
            value["schema_version"], value["puzzle_type"], value["generator_version"],
            int(value["width"]), int(value["height"]),
            tuple(int(item) for item in value["initial"]),
            value.get("ruleset", LIGHTS_RULESET),
        )

    def lit_at(self, cell: Cell) -> int:
        return self.initial[cell_index(self.width, cell)]


@dataclass(frozen=True)
class LightsState:
    puzzle_type: str
    step: int
    lights: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"puzzle_type": self.puzzle_type, "step": self.step, "lights": list(self.lights)}


@dataclass(frozen=True)
class LightsReplayStep:
    action: Action
    before: LightsState
    after: LightsState


@dataclass(frozen=True)
class LightsReplayTrace:
    initial: LightsState
    steps: tuple[LightsReplayStep, ...]
    final: LightsState


def make_action(puzzle: LightsPuzzleSpec, state: LightsState, cell: Cell) -> Action:
    return Action(
        1, "toggle_cell", f"cell-{cell[0]}-{cell[1]}",
        {"cell": [cell[0], cell[1]]},
        {"state_hash": sha256_value(state.to_dict())},
    )


class LightsRules:
    def validate_structure(self, puzzle: LightsPuzzleSpec) -> list[str]:
        errors: list[str] = []
        if puzzle.puzzle_type != "lights":
            errors.append("wrong puzzle type")
        if puzzle.ruleset != LIGHTS_RULESET:
            errors.append("unsupported ruleset")
        if (puzzle.width, puzzle.height) != (BOARD_WIDTH, BOARD_HEIGHT):
            errors.append("board shape is not the 5x4 full-rank shape")
        if len(puzzle.initial) != puzzle.width * puzzle.height:
            errors.append("initial light vector length differs from the cell count")
            return sorted(set(errors))
        if any(value not in (0, 1) for value in puzzle.initial):
            errors.append("initial light value outside {0, 1}")
        if all(value == 1 for value in puzzle.initial):
            errors.append("board is already solved")
        if not errors:
            analysis = _gf2_analysis(puzzle)
            if analysis["nullity"] != 0:
                errors.append("toggle matrix does not have full column rank")
        return sorted(set(errors))

    def initial_state(self, puzzle: LightsPuzzleSpec) -> LightsState:
        return LightsState("lights", 0, tuple(puzzle.initial))

    def legal_actions(self, puzzle: LightsPuzzleSpec, state: LightsState) -> tuple[Action, ...]:
        # Every cell is always pressable; the puzzle is which *set* to press.
        return tuple(
            make_action(puzzle, state, (x, y))
            for y in range(puzzle.height) for x in range(puzzle.width)
        )

    def apply(self, puzzle: LightsPuzzleSpec, state: LightsState, action: Action) -> LightsState:
        if action.kind != "toggle_cell":
            raise ValueError("unsupported action")
        if action.precondition.get("state_hash") != sha256_value(state.to_dict()):
            raise ValueError("action precondition mismatch")
        cell_value = action.params.get("cell", [])
        if len(cell_value) != 2:
            raise ValueError("invalid toggle_cell parameters")
        cell = (int(cell_value[0]), int(cell_value[1]))
        if not (0 <= cell[0] < puzzle.width and 0 <= cell[1] < puzzle.height):
            raise ValueError("illegal press: cell outside the board")
        if action.actor_id != f"cell-{cell[0]}-{cell[1]}":
            raise ValueError("cell actor/id mismatch")
        lights = list(state.lights)
        for neighbour in plus_cells(puzzle.width, puzzle.height, cell):
            index = cell_index(puzzle.width, neighbour)
            lights[index] ^= 1
        return LightsState("lights", state.step + 1, tuple(lights))

    def is_goal(self, puzzle: LightsPuzzleSpec, state: LightsState) -> bool:
        return len(state.lights) == puzzle.width * puzzle.height and all(value == 1 for value in state.lights)


def replay_lights(puzzle: LightsPuzzleSpec, actions: tuple[Action, ...], rules: LightsRules) -> LightsReplayTrace:
    state = rules.initial_state(puzzle)
    initial = state
    steps = []
    for action in actions:
        before = state
        state = rules.apply(puzzle, state, action)
        steps.append(LightsReplayStep(action, before, state))
    return LightsReplayTrace(initial, tuple(steps), state)


# --------------------------------------------------------------------------
# GF(2) linear algebra: rank is the whole proof
# --------------------------------------------------------------------------


def _gf2_analysis(puzzle: LightsPuzzleSpec) -> dict:
    """Solve ``A s = b`` over GF(2) by forward elimination.

    Rows are packed as ints: bit ``c`` is the coefficient of unknown ``c`` and
    bit ``n`` carries the right-hand side. ``nullity == 0`` simultaneously
    proves existence and uniqueness of the press set.
    """
    width, height = puzzle.width, puzzle.height
    count = width * height
    masks = press_masks(width, height)
    # b_r = 1 exactly where the cell is unlit (it must be flipped to reach all-on).
    rows: list[int] = []
    for r in range(count):
        row = 0
        for c in range(count):
            if masks[c] >> r & 1:
                row |= 1 << c
        if puzzle.initial[r] == 0:
            row |= 1 << count
        rows.append(row)
    steps = 0
    pivot_of_column: dict[int, int] = {}
    rank = 0
    for column in range(count):
        pivot = None
        for r in range(rank, count):
            steps += 1
            if rows[r] >> column & 1:
                pivot = r
                break
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        for r in range(count):
            steps += 1
            if r != rank and rows[r] >> column & 1:
                rows[r] ^= rows[rank]
        pivot_of_column[column] = rank
        rank += 1
    nullity = count - rank
    inconsistent = any(row == (1 << count) for row in rows)
    if inconsistent:
        status = "unsolvable"
    elif nullity > 0:
        status = "ambiguous"
    else:
        status = "unique"
    press_set: tuple[Cell, ...] = ()
    if status == "unique":
        cells = []
        for column, row_index in sorted(pivot_of_column.items()):
            if rows[row_index] >> count & 1:
                cells.append((column % width, column // width))
        press_set = tuple(sorted(cells, key=lambda cell: (cell[1], cell[0])))
    return {
        "status": status, "rank": rank, "nullity": nullity,
        "expanded_nodes": steps, "proof": "gf2-full-column-rank",
        "_press_set": press_set,
    }


def press_set_signature(cells: Iterable[Cell]) -> tuple[tuple[int, int], ...]:
    return tuple(sorted((int(x), int(y)) for x, y in cells))


def signature_hash(cells: Iterable[Cell]) -> str:
    return sha256(canonical_json_bytes([list(item) for item in press_set_signature(cells)])).hexdigest()


def _canonical_actions(puzzle: LightsPuzzleSpec, press_set: tuple[Cell, ...], rules: LightsRules) -> tuple[Action, ...]:
    """Presses commute, so the canonical order is simply reading order."""
    state = rules.initial_state(puzzle)
    actions = []
    for cell in sorted(press_set, key=lambda item: (item[1], item[0])):
        action = make_action(puzzle, state, cell)
        state = rules.apply(puzzle, state, action)
        actions.append(action)
    return tuple(actions)


class LightsSolver:
    solver_id = "lights-gf2-unique"
    solver_version = "1"

    def __init__(self, rules: LightsRules):
        self.rules = rules

    def analyze(self, puzzle: LightsPuzzleSpec) -> dict:
        return _gf2_analysis(puzzle)

    def solve(self, puzzle: LightsPuzzleSpec) -> Solution:
        analysis = self.analyze(puzzle)
        public = {key: value for key, value in analysis.items() if not key.startswith("_")}
        if analysis["status"] == "unsolvable":
            raise LightsSolveRejected("UNSOLVABLE_TARGET", public)
        if analysis["status"] != "unique":
            raise LightsSolveRejected("MULTIPLE_PRESS_SETS", public)
        press_set = analysis["_press_set"]
        state = self.rules.initial_state(puzzle)
        initial_hash = sha256_value(state.to_dict())
        actions = _canonical_actions(puzzle, press_set, self.rules)
        trace = replay_lights(puzzle, actions, self.rules)
        if not self.rules.is_goal(puzzle, trace.final):
            raise LightsSolveRejected("SOLVER_INTERNAL_ERROR", public)
        return Solution(
            "1.0.0", self.solver_id, self.solver_version, "proven_unique_gf2_press_set",
            actions, initial_hash, sha256_value(trace.final.to_dict()),
            len(actions), analysis["expanded_nodes"], "unique:" + signature_hash(press_set),
        )


def solution_press_set(solution: Solution) -> tuple[Cell, ...]:
    return tuple((action.params["cell"][0], action.params["cell"][1]) for action in solution.actions)


def validate_lights_solution(puzzle: LightsPuzzleSpec, solution: Solution, rules: LightsRules) -> list[str]:
    failures = rules.validate_structure(puzzle)
    if failures:
        return failures
    try:
        trace = replay_lights(puzzle, solution.actions, rules)
    except ValueError as error:
        failures.append(f"illegal action: {error}")
        return failures
    if not rules.is_goal(puzzle, trace.final):
        failures.append("board is not fully lit")
    if sha256_value(trace.initial.to_dict()) != solution.initial_state_hash:
        failures.append("initial state hash mismatch")
    if sha256_value(trace.final.to_dict()) != solution.final_state_hash:
        failures.append("final state hash mismatch")
    if solution.cost != len(solution.actions):
        failures.append("solution cost mismatch")
    presses = solution_press_set(solution)
    if len(set(presses)) != len(presses):
        failures.append("solution presses a cell more than once")
    analysis = LightsSolver(rules).analyze(puzzle)
    if analysis["status"] != "unique":
        failures.append("press set is not provably unique")
    elif solution.answer_equivalence_key != "unique:" + signature_hash(presses):
        failures.append("answer equivalence key mismatch")
    elif press_set_signature(presses) != press_set_signature(analysis["_press_set"]):
        failures.append("solution differs from the unique press set oracle")
    return failures


# --------------------------------------------------------------------------
# Generation (press k cells from the all-lit board, then a quality screen)
# --------------------------------------------------------------------------


def board_from_presses(width: int, height: int, presses: Iterable[Cell]) -> tuple[int, ...]:
    lights = [1] * (width * height)
    for cell in presses:
        for neighbour in plus_cells(width, height, cell):
            lights[cell_index(width, neighbour)] ^= 1
    return tuple(lights)


def draw_candidate(rng: StableRng, preset: dict) -> LightsPuzzleSpec:
    """One unscreened board. Exported so tests can exercise rejection paths."""
    width = int(preset.get("width", BOARD_WIDTH))
    height = int(preset.get("height", BOARD_HEIGHT))
    press_count = int(preset.get("press_count", 4))
    cells = [(x, y) for y in range(height) for x in range(width)]
    if not 1 <= press_count <= len(cells):
        raise ValueError("LIGHTS_PRESS_COUNT_INVALID: press count outside the board size")
    rng.shuffle(cells)
    presses = tuple(sorted(cells[:press_count], key=lambda item: (item[1], item[0])))
    initial = board_from_presses(width, height, presses)
    if all(value == 1 for value in initial):
        raise ValueError("LIGHTS_DEGENERATE_BOARD: press set leaves the board already solved")
    return LightsPuzzleSpec("1.0.0", "lights", "lights-gen-1", width, height, initial, LIGHTS_RULESET)


def generate_lights(rng: StableRng, preset: dict) -> LightsPuzzleSpec:
    """Backwards generation with an in-generator quality screen.

    Because the 5x4 toggle matrix is invertible, the drawn press set is already
    the unique solution; the screen only rejects boards that are too easy, too
    symmetric or otherwise outside the band. Nothing produced here is trusted:
    the pipeline re-solves and re-filters every candidate.
    """
    rules = LightsRules()
    solver = LightsSolver(rules)
    attempts = int(preset.get("search_attempts", 400))
    fallback: LightsPuzzleSpec | None = None
    for _ in range(max(1, attempts)):
        try:
            candidate = draw_candidate(rng, preset)
        except ValueError:
            continue
        if rules.validate_structure(candidate):
            continue
        if fallback is None:
            fallback = candidate
        try:
            solution = solver.solve(candidate)
        except LightsSolveRejected:
            continue
        report = lights_difficulty_report(candidate, solution, rules)
        if lights_quality_rejection(report, preset.get("band", "medium")) is None:
            return candidate
    if fallback is None:
        raise ValueError("LIGHTS_LAYOUT_FAILED: no structurally valid board was produced")
    return fallback


# --------------------------------------------------------------------------
# Difficulty
# --------------------------------------------------------------------------


def _clusters(width: int, height: int, lights: tuple[int, ...], value: int) -> int:
    """4-connected components of cells whose light equals ``value``."""
    members = {(x, y) for y in range(height) for x in range(width) if lights[y * width + x] == value}
    seen: set[Cell] = set()
    count = 0
    for cell in sorted(members):
        if cell in seen:
            continue
        count += 1
        stack = [cell]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            x, y = current
            for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if neighbour in members and neighbour not in seen:
                    stack.append(neighbour)
    return count


def greedy_reduction_count(puzzle: LightsPuzzleSpec) -> int:
    """Presses that immediately reduce the number of unlit cells.

    Few such presses means the board is deceptive: almost every locally
    attractive move makes the board worse before it gets better.
    """
    width, height = puzzle.width, puzzle.height
    unlit = sum(1 for value in puzzle.initial if value == 0)
    count = 0
    for y in range(height):
        for x in range(width):
            lights = list(puzzle.initial)
            for neighbour in plus_cells(width, height, (x, y)):
                lights[cell_index(width, neighbour)] ^= 1
            if sum(1 for value in lights if value == 0) < unlit:
                count += 1
    return count


def chase_press_count(puzzle: LightsPuzzleSpec) -> int:
    """Presses a naive top-down light-chase would make below the first row."""
    width, height = puzzle.width, puzzle.height
    lights = list(puzzle.initial)
    presses = 0
    for y in range(height - 1):
        for x in range(width):
            if lights[y * width + x] == 0:
                presses += 1
                for neighbour in plus_cells(width, height, (x, y + 1)):
                    lights[cell_index(width, neighbour)] ^= 1
    return presses


def board_symmetries(puzzle: LightsPuzzleSpec) -> list[str]:
    """Mirror/rotation symmetries of the lit pattern; a symmetric board is trivial."""
    width, height = puzzle.width, puzzle.height
    grid = [[puzzle.initial[y * width + x] for x in range(width)] for y in range(height)]
    found = []
    if all(grid[y][x] == grid[y][width - 1 - x] for y in range(height) for x in range(width)):
        found.append("horizontal_mirror")
    if all(grid[y][x] == grid[height - 1 - y][x] for y in range(height) for x in range(width)):
        found.append("vertical_mirror")
    if all(grid[y][x] == grid[height - 1 - y][width - 1 - x] for y in range(height) for x in range(width)):
        found.append("half_turn")
    return found


def lights_difficulty_preset(band: str) -> dict:
    from .preset_loader import difficulty_preset as load_difficulty_preset
    return load_difficulty_preset("lights", band)


def lights_difficulty_report(puzzle: LightsPuzzleSpec, solution: Solution, rules: LightsRules) -> dict:
    analysis = LightsSolver(rules).analyze(puzzle)
    presses = solution_press_set(solution)
    lights = tuple(puzzle.initial)
    lit = sum(1 for value in lights if value == 1)
    unlit = len(lights) - lit
    lit_clusters = _clusters(puzzle.width, puzzle.height, lights, 1)
    unlit_clusters = _clusters(puzzle.width, puzzle.height, lights, 0)
    greedy = greedy_reduction_count(puzzle)
    chase = chase_press_count(puzzle)
    symmetries = board_symmetries(puzzle)
    mechanical = {
        "press_count": len(presses),
        "lit_cells": lit,
        "unlit_cells": unlit,
        "lit_clusters": lit_clusters,
        "unlit_clusters": unlit_clusters,
        "greedy_reduction_count": greedy,
        "chase_press_count": chase,
        "board_symmetries": symmetries,
        "board_cells": puzzle.width * puzzle.height,
        "solver_expanded_nodes": solution.expanded_nodes,
        "gf2_rank": analysis["rank"],
        "gf2_nullity": analysis["nullity"],
    }
    # |S| is 2 / 3 / 4, so press_count alone only spreads the bands 12 points
    # apart - far less than the spread of a single band. The deceptiveness
    # terms therefore carry the separation: how few greedy presses help, how
    # scattered the lit and unlit regions are, how far a naive chase drifts.
    # Their weights were raised (4 -> 6, 3 -> 4, 2 -> 4, 1 -> 2) when the press
    # bands were re-cut from 4/6/8 to 2/3/4; measured over a generation batch
    # the accepted bands stay non-overlapping (92-116 / 118-152 / 154-172).
    mechanical["difficulty_score"] = (
        12 * mechanical["press_count"]
        + 6 * mechanical["lit_clusters"]
        + 4 * mechanical["unlit_clusters"]
        + 4 * (puzzle.width * puzzle.height - greedy)
        + 2 * chase
        - 6 * len(symmetries)
    )
    return {
        "mechanical": mechanical,
        "human": {
            "status": "uncalibrated-lights-v1", "model_version": None,
            "calibration_scope": None, "predicted_correct_time_ms": None,
            "p_solve_before_reveal": None,
            "features": {
                "press_count": mechanical["press_count"],
                "lit_clusters": lit_clusters,
                "unlit_clusters": unlit_clusters,
                "greedy_reduction_count": greedy,
                "chase_press_count": chase,
            },
        },
        "solution_uniqueness": {
            "status": analysis["status"],
            "press_set_count": 1 if analysis["status"] == "unique" else 0,
            "rank": analysis["rank"],
            "nullity": analysis["nullity"],
            "proof": analysis["proof"],
            "expanded_nodes": analysis["expanded_nodes"],
            "equivalence_policy": LIGHTS_EQUIVALENCE_POLICY,
            "equivalence_policy_version": LIGHTS_EQUIVALENCE_VERSION,
            "normalized_signature": [list(item) for item in press_set_signature(presses)],
            "normalized_signature_hash": signature_hash(presses),
        },
        "requested_band": None, "accepted_band": None, "quality_preset": None,
    }


def lights_quality_rejection(difficulty: dict, requested_band: str = "medium") -> str | None:
    preset = lights_difficulty_preset(requested_band)
    metrics = difficulty["mechanical"]
    difficulty["requested_band"] = requested_band
    difficulty["quality_preset"] = preset["name"]
    uniqueness = difficulty.get("solution_uniqueness")
    if uniqueness is not None and uniqueness["status"] != "unique":
        return "MULTIPLE_PRESS_SETS" if uniqueness["status"] == "ambiguous" else "UNSOLVABLE_TARGET"
    # The neutrality perturbation is a cyclic shift of the press order, so
    # fewer than two presses is rejected in every band: with two or more
    # distinct press cells the shift always changes the first press.
    if metrics["press_count"] < max(MIN_PRESS_COUNT, preset["min_presses"]):
        return "PRESS_COUNT_TOO_LOW"
    if metrics["press_count"] > preset["max_presses"]:
        return "ANIMATION_TOO_LONG"
    if metrics["board_symmetries"]:
        return "SYMMETRIC_BOARD"
    if metrics["lit_clusters"] < preset["min_lit_clusters"]:
        return "TOO_TRIVIAL"
    if metrics["greedy_reduction_count"] > preset["max_greedy_reductions"]:
        return "TOO_GREEDY_FRIENDLY"
    if metrics["difficulty_score"] < preset["min_difficulty_score"]:
        return "TOO_TRIVIAL"
    if metrics["difficulty_score"] > preset["max_difficulty_score"]:
        return "OUTSIDE_BAND"
    difficulty["accepted_band"] = requested_band
    difficulty["human"]["status"] = "uncalibrated-lights-v1"
    return None
