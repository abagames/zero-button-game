"""Polyomino packing (exact cover, translation only) puzzle logic.

A hole is a set of cells inside a small grid. A fixed set of polyomino pieces
must be moved out of a tray and into the hole so that every hole cell is
covered exactly once. Pieces never rotate or reflect: a piece is a fixed
shape that can only be translated, so the ``move_piece`` action carries a
destination anchor and nothing else.

Uniqueness policy: a candidate is accepted only when exactly one exact cover
exists. Two pieces with identical shapes are interchangeable, so a cover is
identified by the multiset of ``(shape, anchor)`` placements rather than by
piece identity; the search itself enumerates shapes, so interchangeable
swaps are never counted as separate covers. The search stops as soon as a
second cover is found.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any, Iterable

from .core import StableRng, canonical_json_bytes, sha256_value
from .models import Action, Solution

Cell = tuple[int, int]
Shape = tuple[Cell, ...]
Piece = tuple[int, Shape]
Placement = tuple[Shape, Cell]

PACKING_RULESET = "exact-cover-norotate-v1"
PACKING_EQUIVALENCE_POLICY = "multiset of (piece shape, placement anchor) over the unique exact cover"
PACKING_EQUIVALENCE_VERSION = "packing-shape-anchor-cover-v1"
UNPLACED: Cell = (-1, -1)

# Rendering couples the tray to the hole: the tray is a single row under the
# hole, so every piece must fit in a 3x2 bounding box and the summed bounding
# box widths must fit the 9-cell tray strip. Both limits are structural, not cosmetic.
MAX_PIECE_WIDTH = 3
MAX_PIECE_HEIGHT = 2
MAX_TRAY_WIDTH_CELLS = 9
SOLVE_NODE_BUDGET = 2_000_000


class PackingSolveRejected(RuntimeError):
    def __init__(self, code: str, diagnostics: dict | None = None):
        super().__init__(code)
        self.code = code
        self.diagnostics = diagnostics or {}


def normalize_shape(cells: Iterable[Cell]) -> Shape:
    items = sorted(set(cells))
    if not items:
        return ()
    min_x = min(x for x, _ in items)
    min_y = min(y for _, y in items)
    return tuple(sorted((x - min_x, y - min_y) for x, y in items))


def _connected(cells: Iterable[Cell]) -> bool:
    remaining = set(cells)
    if not remaining:
        return False
    stack = [next(iter(remaining))]
    seen = set()
    while stack:
        cell = stack.pop()
        if cell in seen or cell not in remaining:
            continue
        seen.add(cell)
        x, y = cell
        stack.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)])
    return len(seen) == len(remaining)


def _build_shape_family() -> tuple[Shape, ...]:
    """Every connected 3- or 4-cell polyomino inside a 3x2 box (no rotation)."""
    box = [(x, y) for y in range(MAX_PIECE_HEIGHT) for x in range(MAX_PIECE_WIDTH)]
    found: set[Shape] = set()
    for mask in range(1, 1 << len(box)):
        cells = [box[index] for index in range(len(box)) if mask >> index & 1]
        if not 3 <= len(cells) <= 4 or not _connected(cells):
            continue
        found.add(normalize_shape(cells))
    return tuple(sorted(found))


SHAPE_FAMILY = _build_shape_family()


def shape_bbox(shape: Shape) -> tuple[int, int]:
    return max(x for x, _ in shape) + 1, max(y for _, y in shape) + 1


def shape_key(shape: Shape) -> str:
    return ";".join(f"{x},{y}" for x, y in shape)


@dataclass(frozen=True)
class PackingPuzzleSpec:
    schema_version: str
    puzzle_type: str
    generator_version: str
    width: int
    height: int
    hole_cells: tuple[Cell, ...]
    pieces: tuple[Piece, ...]
    tray_slots: tuple[Cell, ...]
    ruleset: str = PACKING_RULESET

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["hole_cells"] = [list(cell) for cell in self.hole_cells]
        value["pieces"] = [[piece_id, [list(cell) for cell in shape]] for piece_id, shape in self.pieces]
        value["tray_slots"] = [list(slot) for slot in self.tray_slots]
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PackingPuzzleSpec":
        return cls(
            value["schema_version"], value["puzzle_type"], value["generator_version"],
            int(value["width"]), int(value["height"]),
            tuple((int(x), int(y)) for x, y in value["hole_cells"]),
            tuple((int(piece_id), tuple((int(x), int(y)) for x, y in shape)) for piece_id, shape in value["pieces"]),
            tuple((int(x), int(y)) for x, y in value["tray_slots"]),
            value.get("ruleset", PACKING_RULESET),
        )

    def index_of(self, piece_id: int) -> int:
        for index, (candidate, _) in enumerate(self.pieces):
            if candidate == piece_id:
                return index
        raise ValueError(f"unknown piece id: {piece_id}")

    def hole(self) -> frozenset[Cell]:
        return frozenset(self.hole_cells)


@dataclass(frozen=True)
class PackingState:
    puzzle_type: str
    step: int
    placements: tuple[Cell, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"puzzle_type": self.puzzle_type, "step": self.step, "placements": [list(item) for item in self.placements]}


@dataclass(frozen=True)
class PackingReplayStep:
    action: Action
    before: PackingState
    after: PackingState


@dataclass(frozen=True)
class PackingReplayTrace:
    initial: PackingState
    steps: tuple[PackingReplayStep, ...]
    final: PackingState


def placed_cells(shape: Shape, anchor: Cell) -> tuple[Cell, ...]:
    return tuple((anchor[0] + x, anchor[1] + y) for x, y in shape)


def covered_cells(puzzle: PackingPuzzleSpec, placements: tuple[Cell, ...]) -> set[Cell]:
    cells: set[Cell] = set()
    for index, anchor in enumerate(placements):
        if anchor == UNPLACED:
            continue
        cells.update(placed_cells(puzzle.pieces[index][1], anchor))
    return cells


def anchors_for(shape: Shape, hole: frozenset[Cell], covered: set[Cell]) -> list[Cell]:
    """Every anchor putting ``shape`` entirely into free hole cells."""
    xs = {x for x, _ in hole}
    ys = {y for _, y in hole}
    width, height = shape_bbox(shape)
    result = []
    for y in range(min(ys), max(ys) - height + 2):
        for x in range(min(xs), max(xs) - width + 2):
            cells = placed_cells(shape, (x, y))
            if all(cell in hole and cell not in covered for cell in cells):
                result.append((x, y))
    return result


def make_action(puzzle: PackingPuzzleSpec, state: PackingState, index: int, anchor: Cell) -> Action:
    piece_id, shape = puzzle.pieces[index]
    return Action(
        1, "move_piece", f"piece-{piece_id}",
        {"piece": [piece_id], "to": [anchor[0], anchor[1]], "cells": [len(shape)]},
        {"state_hash": sha256_value(state.to_dict())},
    )


class PackingRules:
    def validate_structure(self, puzzle: PackingPuzzleSpec) -> list[str]:
        errors: list[str] = []
        if puzzle.puzzle_type != "packing":
            errors.append("wrong puzzle type")
        if puzzle.ruleset != PACKING_RULESET:
            errors.append("unsupported ruleset")
        if not (3 <= puzzle.width <= 5 and 3 <= puzzle.height <= 4):
            errors.append("hole grid outside 3..5 x 3..4")
        hole = set(puzzle.hole_cells)
        if len(hole) != len(puzzle.hole_cells):
            errors.append("duplicate hole cell")
        if not hole:
            errors.append("empty hole")
            return sorted(set(errors))
        if any(not (0 <= x < puzzle.width and 0 <= y < puzzle.height) for x, y in hole):
            errors.append("hole cell outside the grid")
        if not _connected(hole):
            errors.append("hole is not connected")
        if not puzzle.pieces:
            errors.append("no pieces")
            return sorted(set(errors))
        ids = [piece_id for piece_id, _ in puzzle.pieces]
        if len(set(ids)) != len(ids):
            errors.append("duplicate piece id")
        if len(puzzle.tray_slots) != len(puzzle.pieces):
            errors.append("tray slot count differs from piece count")
        tray_width = 0
        for _, shape in puzzle.pieces:
            if normalize_shape(shape) != tuple(sorted(shape)):
                errors.append("piece shape is not normalized")
                continue
            if not _connected(shape):
                errors.append("piece is not connected")
                continue
            if not 3 <= len(shape) <= 4:
                errors.append("piece size outside 3..4")
                continue
            width, height = shape_bbox(shape)
            if width > MAX_PIECE_WIDTH or height > MAX_PIECE_HEIGHT:
                errors.append("piece bounding box exceeds 3x2")
            tray_width += width
        if tray_width > MAX_TRAY_WIDTH_CELLS:
            errors.append("tray row is wider than the readable strip")
        if sum(len(shape) for _, shape in puzzle.pieces) != len(hole):
            errors.append("piece area differs from hole area")
        return sorted(set(errors))

    def initial_state(self, puzzle: PackingPuzzleSpec) -> PackingState:
        return PackingState("packing", 0, (UNPLACED,) * len(puzzle.pieces))

    def legal_actions(self, puzzle: PackingPuzzleSpec, state: PackingState) -> tuple[Action, ...]:
        hole = puzzle.hole()
        covered = covered_cells(puzzle, state.placements)
        actions = []
        for index, (_, shape) in enumerate(puzzle.pieces):
            if state.placements[index] != UNPLACED:
                continue
            for anchor in anchors_for(shape, hole, covered):
                actions.append(make_action(puzzle, state, index, anchor))
        return tuple(actions)

    def apply(self, puzzle: PackingPuzzleSpec, state: PackingState, action: Action) -> PackingState:
        if action.kind != "move_piece" or not action.actor_id.startswith("piece-"):
            raise ValueError("unsupported action")
        if action.precondition.get("state_hash") != sha256_value(state.to_dict()):
            raise ValueError("action precondition mismatch")
        piece_value = action.params.get("piece", [])
        to_value = action.params.get("to", [])
        cells_value = action.params.get("cells", [])
        if len(piece_value) != 1 or len(to_value) != 2 or len(cells_value) != 1:
            raise ValueError("invalid move_piece parameters")
        piece_id = piece_value[0]
        try:
            index = puzzle.index_of(piece_id)
        except ValueError as error:
            raise ValueError("illegal piece placement") from error
        if action.actor_id != f"piece-{piece_id}":
            raise ValueError("piece actor/id mismatch")
        shape = puzzle.pieces[index][1]
        if cells_value[0] != len(shape):
            raise ValueError("invalid move_piece parameters")
        if state.placements[index] != UNPLACED:
            raise ValueError("illegal piece placement: piece already placed")
        anchor = (to_value[0], to_value[1])
        hole = puzzle.hole()
        covered = covered_cells(puzzle, state.placements)
        cells = placed_cells(shape, anchor)
        if any(cell not in hole for cell in cells):
            raise ValueError("illegal piece placement: outside the hole")
        if any(cell in covered for cell in cells):
            raise ValueError("illegal piece placement: overlap")
        updated = list(state.placements)
        updated[index] = anchor
        return PackingState("packing", state.step + 1, tuple(updated))

    def is_goal(self, puzzle: PackingPuzzleSpec, state: PackingState) -> bool:
        if any(anchor == UNPLACED for anchor in state.placements):
            return False
        return covered_cells(puzzle, state.placements) == puzzle.hole()


def replay_packing(puzzle: PackingPuzzleSpec, actions: tuple[Action, ...], rules: PackingRules) -> PackingReplayTrace:
    state = rules.initial_state(puzzle)
    initial = state
    steps = []
    for action in actions:
        before = state
        state = rules.apply(puzzle, state, action)
        steps.append(PackingReplayStep(action, before, state))
    return PackingReplayTrace(initial, tuple(steps), state)


# --------------------------------------------------------------------------
# Exact-cover search
# --------------------------------------------------------------------------


def _shape_counts(puzzle: PackingPuzzleSpec) -> tuple[tuple[Shape, int], ...]:
    counts: dict[Shape, int] = {}
    for _, shape in puzzle.pieces:
        counts[shape] = counts.get(shape, 0) + 1
    return tuple(sorted(counts.items()))


def cover_signature(placements: Iterable[Placement]) -> tuple[tuple[str, int, int], ...]:
    """Interchange-normalized identity of a cover: shapes sorted, then anchors."""
    return tuple(sorted((shape_key(shape), anchor[0], anchor[1]) for shape, anchor in placements))


def search_covers(
    puzzle: PackingPuzzleSpec, limit: int = 2, forced: Placement | None = None,
    node_budget: int = SOLVE_NODE_BUDGET,
) -> dict:
    """Enumerate exact covers up to ``limit``; returns covers plus node count."""
    hole = puzzle.hole()
    counts = {shape: count for shape, count in _shape_counts(puzzle)}
    order = sorted(hole, key=lambda cell: (cell[1], cell[0]))
    covers: list[tuple[Placement, ...]] = []
    stats = {"nodes": 0}

    def recurse(covered: set[Cell], chosen: list[Placement]) -> bool:
        """True when the search must stop (limit or budget reached)."""
        stats["nodes"] += 1
        if stats["nodes"] > node_budget:
            raise PackingSolveRejected("SOLVE_BUDGET_EXCEEDED", {"nodes": stats["nodes"]})
        if len(covered) == len(hole):
            covers.append(tuple(chosen))
            return len(covers) >= limit
        target = next(cell for cell in order if cell not in covered)
        for shape in sorted(counts):
            if counts[shape] <= 0:
                continue
            for offset in shape:
                anchor = (target[0] - offset[0], target[1] - offset[1])
                cells = placed_cells(shape, anchor)
                if any(cell not in hole or cell in covered for cell in cells):
                    continue
                counts[shape] -= 1
                covered.update(cells)
                chosen.append((shape, anchor))
                stop = recurse(covered, chosen)
                chosen.pop()
                covered.difference_update(cells)
                counts[shape] += 1
                if stop:
                    return True
        return False

    covered_start: set[Cell] = set()
    chosen_start: list[Placement] = []
    if forced is not None:
        shape, anchor = forced
        cells = placed_cells(shape, anchor)
        if counts.get(shape, 0) <= 0 or any(cell not in hole for cell in cells):
            return {"covers": [], "nodes": 0}
        counts[shape] -= 1
        covered_start.update(cells)
        chosen_start.append((shape, anchor))
    recurse(covered_start, chosen_start)
    return {"covers": covers, "nodes": stats["nodes"]}


def _canonical_actions(puzzle: PackingPuzzleSpec, cover: tuple[Placement, ...], rules: PackingRules) -> tuple[Action, ...]:
    """Map a shape-keyed cover onto concrete pieces, in top-left fill order."""
    by_shape: dict[Shape, list[int]] = {}
    for index, (piece_id, shape) in enumerate(puzzle.pieces):
        by_shape.setdefault(shape, []).append(index)
    for shape in by_shape:
        by_shape[shape].sort(key=lambda index: puzzle.pieces[index][0])
    assignment: list[tuple[Cell, int, Cell]] = []
    for shape, anchor in sorted(cover, key=lambda item: (shape_key(item[0]), item[1][1], item[1][0])):
        index = by_shape[shape].pop(0)
        head = min(placed_cells(shape, anchor), key=lambda cell: (cell[1], cell[0]))
        assignment.append((head, index, anchor))
    assignment.sort()
    state = rules.initial_state(puzzle)
    actions = []
    for _, index, anchor in assignment:
        action = make_action(puzzle, state, index, anchor)
        state = rules.apply(puzzle, state, action)
        actions.append(action)
    return tuple(actions)


def signature_hash(cover: Iterable[Placement]) -> str:
    return sha256(canonical_json_bytes([list(item) for item in cover_signature(cover)])).hexdigest()


class PackingSolver:
    solver_id = "packing-exact-cover-unique"
    solver_version = "1"

    def __init__(self, rules: PackingRules):
        self.rules = rules

    def analyze(self, puzzle: PackingPuzzleSpec, node_budget: int = SOLVE_NODE_BUDGET) -> dict:
        result = search_covers(puzzle, limit=2, node_budget=node_budget)
        covers = result["covers"]
        if not covers:
            return {"status": "unsolvable", "cover_count": 0, "expanded_nodes": result["nodes"]}
        if len(covers) > 1:
            return {"status": "ambiguous", "cover_count": len(covers), "expanded_nodes": result["nodes"]}
        return {"status": "unique", "cover_count": 1, "expanded_nodes": result["nodes"], "_cover": covers[0]}

    def solve(self, puzzle: PackingPuzzleSpec, node_budget: int = SOLVE_NODE_BUDGET) -> Solution:
        analysis = self.analyze(puzzle, node_budget)
        public = {key: value for key, value in analysis.items() if not key.startswith("_")}
        if analysis["status"] == "unsolvable":
            raise PackingSolveRejected("UNSOLVABLE", public)
        if analysis["status"] != "unique":
            raise PackingSolveRejected("MULTIPLE_COVERS", public)
        cover = analysis["_cover"]
        state = self.rules.initial_state(puzzle)
        initial_hash = sha256_value(state.to_dict())
        actions = _canonical_actions(puzzle, cover, self.rules)
        trace = replay_packing(puzzle, actions, self.rules)
        if not self.rules.is_goal(puzzle, trace.final):
            raise PackingSolveRejected("SOLVER_INTERNAL_ERROR", public)
        return Solution(
            "1.0.0", self.solver_id, self.solver_version, "proven_unique_exact_cover",
            actions, initial_hash, sha256_value(trace.final.to_dict()),
            len(actions), analysis["expanded_nodes"], "unique:" + signature_hash(cover),
        )


def solution_cover(puzzle: PackingPuzzleSpec, solution: Solution) -> tuple[Placement, ...]:
    return tuple(
        (puzzle.pieces[puzzle.index_of(action.params["piece"][0])][1], (action.params["to"][0], action.params["to"][1]))
        for action in solution.actions
    )


def validate_packing_solution(puzzle: PackingPuzzleSpec, solution: Solution, rules: PackingRules) -> list[str]:
    failures = rules.validate_structure(puzzle)
    if failures:
        return failures
    try:
        trace = replay_packing(puzzle, solution.actions, rules)
    except ValueError as error:
        failures.append(f"illegal action: {error}")
        return failures
    if not rules.is_goal(puzzle, trace.final):
        failures.append("pieces do not cover the hole exactly")
    if sha256_value(trace.initial.to_dict()) != solution.initial_state_hash:
        failures.append("initial state hash mismatch")
    if sha256_value(trace.final.to_dict()) != solution.final_state_hash:
        failures.append("final state hash mismatch")
    if solution.cost != len(solution.actions):
        failures.append("solution cost mismatch")
    if len(solution.actions) != len(puzzle.pieces):
        failures.append("solution does not place every piece exactly once")
    analysis = PackingSolver(rules).analyze(puzzle)
    if analysis["status"] != "unique":
        failures.append("exact cover is not unique")
    elif solution.answer_equivalence_key != "unique:" + signature_hash(solution_cover(puzzle, solution)):
        failures.append("answer equivalence key mismatch")
    elif cover_signature(solution_cover(puzzle, solution)) != cover_signature(analysis["_cover"]):
        failures.append("solution differs from the unique cover oracle")
    return failures


# --------------------------------------------------------------------------
# Generation (random tiling, then a uniqueness screen)
# --------------------------------------------------------------------------


def _tray_slots(shapes: Iterable[Shape]) -> tuple[Cell, ...]:
    slots = []
    cursor = 0
    for shape in shapes:
        slots.append((cursor, 0))
        cursor += shape_bbox(shape)[0]
    return tuple(slots)


def _spec_from_tiling(width: int, height: int, tiling: list[Placement]) -> PackingPuzzleSpec:
    ordered = sorted(tiling, key=lambda item: (shape_key(item[0]), item[1][1], item[1][0]))
    shapes = [shape for shape, _ in ordered]
    hole: list[Cell] = []
    for shape, anchor in ordered:
        hole.extend(placed_cells(shape, anchor))
    return PackingPuzzleSpec(
        "1.0.0", "packing", "packing-gen-1", width, height,
        tuple(sorted(hole)), tuple((index, shape) for index, shape in enumerate(shapes)),
        _tray_slots(shapes), PACKING_RULESET,
    )


def draw_candidate(rng: StableRng, preset: dict) -> PackingPuzzleSpec:
    """One unscreened tiling. Exported so tests can exercise rejection paths."""
    width = int(preset.get("width", 4))
    height = int(preset.get("height", 4))
    piece_count = int(preset.get("piece_count", 4))
    cells = [(x, y) for y in range(height) for x in range(width)]
    covered: set[Cell] = set()
    tiling: list[Placement] = []
    attempts = 0
    while len(tiling) < piece_count and attempts < 400:
        attempts += 1
        shape = SHAPE_FAMILY[rng.randbelow(len(SHAPE_FAMILY))]
        bw, bh = shape_bbox(shape)
        if bw > width or bh > height:
            continue
        anchors = [
            (x, y) for y in range(height - bh + 1) for x in range(width - bw + 1)
            if not any(cell in covered for cell in placed_cells(shape, (x, y)))
        ]
        if tiling:
            # Prefer anchors touching the growing hole so it stays one region.
            frontier = [
                anchor for anchor in anchors
                if any(
                    (cx + dx, cy + dy) in covered
                    for cx, cy in placed_cells(shape, anchor)
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                )
            ]
            anchors = frontier or anchors
        if not anchors:
            continue
        anchor = anchors[rng.randbelow(len(anchors))]
        covered.update(placed_cells(shape, anchor))
        tiling.append((shape, anchor))
    if len(tiling) < piece_count:
        raise ValueError("PACKING_TILING_FAILED: could not place the requested pieces")
    return _spec_from_tiling(width, height, tiling)


def generate_packing(rng: StableRng, preset: dict) -> PackingPuzzleSpec:
    """Random tiling with an in-generator uniqueness and quality screen.

    A random tiling always has at least one cover (the tiling itself), so the
    screen only has to reject the many tilings whose hole admits a second
    cover. Nothing produced here is trusted: the pipeline re-solves and
    re-filters every candidate.
    """
    rules = PackingRules()
    solver = PackingSolver(rules)
    attempts = int(preset.get("search_attempts", 400))
    fallback: PackingPuzzleSpec | None = None
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
            analysis = solver.analyze(candidate, int(preset.get("solve_node_budget", SOLVE_NODE_BUDGET)))
        except PackingSolveRejected:
            continue
        if analysis["status"] != "unique":
            continue
        solution = solver.solve(candidate)
        report = packing_difficulty_report(candidate, solution, rules)
        if packing_quality_rejection(report, preset.get("band", "medium")) is None:
            return candidate
    if fallback is None:
        raise ValueError("PACKING_LAYOUT_FAILED: no structurally valid tiling was produced")
    return fallback


# --------------------------------------------------------------------------
# Difficulty
# --------------------------------------------------------------------------


def all_initial_placements(puzzle: PackingPuzzleSpec) -> list[Placement]:
    hole = puzzle.hole()
    shapes = sorted({shape for _, shape in puzzle.pieces})
    return [(shape, anchor) for shape in shapes for anchor in anchors_for(shape, hole, set())]


def dead_placements(puzzle: PackingPuzzleSpec) -> int:
    """Placements that are locally legal but appear in no complete cover."""
    dead = 0
    for placement in all_initial_placements(puzzle):
        try:
            result = search_covers(puzzle, limit=1, forced=placement)
        except PackingSolveRejected:
            continue
        if not result["covers"]:
            dead += 1
    return dead


def greedy_solvable(puzzle: PackingPuzzleSpec) -> bool:
    """Largest-piece-first, top-left-first placement; no backtracking."""
    hole = puzzle.hole()
    counts: dict[Shape, int] = {}
    for _, shape in puzzle.pieces:
        counts[shape] = counts.get(shape, 0) + 1
    covered: set[Cell] = set()
    order = sorted(hole, key=lambda cell: (cell[1], cell[0]))
    while len(covered) < len(hole):
        target = next(cell for cell in order if cell not in covered)
        chosen = None
        for shape in sorted(counts, key=lambda item: (-len(item), shape_key(item))):
            if counts[shape] <= 0:
                continue
            for offset in shape:
                anchor = (target[0] - offset[0], target[1] - offset[1])
                cells = placed_cells(shape, anchor)
                if all(cell in hole and cell not in covered for cell in cells):
                    chosen = (shape, cells)
                    break
            if chosen:
                break
        if chosen is None:
            return False
        counts[chosen[0]] -= 1
        covered.update(chosen[1])
    return True


def packing_difficulty_preset(band: str) -> dict:
    from .preset_loader import difficulty_preset as load_difficulty_preset
    return load_difficulty_preset("packing", band)


def packing_difficulty_report(puzzle: PackingPuzzleSpec, solution: Solution, rules: PackingRules) -> dict:
    shapes = [shape for _, shape in puzzle.pieces]
    concave = sum(1 for shape in shapes if len(shape) != shape_bbox(shape)[0] * shape_bbox(shape)[1])
    duplication = len(shapes) - len({shape for shape in shapes})
    legal = len(all_initial_placements(puzzle))
    dead = dead_placements(puzzle)
    greedy = greedy_solvable(puzzle)
    analysis = PackingSolver(rules).analyze(puzzle)
    cover = solution_cover(puzzle, solution)
    mechanical = {
        "piece_count": len(shapes),
        "hole_cells": len(puzzle.hole_cells),
        "concave_pieces": concave,
        "shape_duplication": duplication,
        "distinct_shapes": len({shape for shape in shapes}),
        "legal_initial_placements": legal,
        "dead_placements": dead,
        "greedy_solvable": greedy,
        "tray_width_cells": sum(shape_bbox(shape)[0] for shape in shapes),
        "grid_cells": puzzle.width * puzzle.height,
        "solver_expanded_nodes": solution.expanded_nodes,
    }
    mechanical["difficulty_score"] = (
        8 * mechanical["piece_count"]
        + 6 * mechanical["concave_pieces"]
        + 5 * mechanical["shape_duplication"]
        + 2 * mechanical["dead_placements"]
        + mechanical["hole_cells"]
        + (0 if greedy else 14)
    )
    return {
        "mechanical": mechanical,
        "human": {
            "status": "uncalibrated-packing-v1", "model_version": None,
            "calibration_scope": None, "predicted_correct_time_ms": None,
            "p_solve_before_reveal": None,
            "features": {
                "piece_count": mechanical["piece_count"],
                "concave_pieces": mechanical["concave_pieces"],
                "shape_duplication": mechanical["shape_duplication"],
                "dead_placements": mechanical["dead_placements"],
                "greedy_solvable": greedy,
            },
        },
        "solution_uniqueness": {
            "status": analysis["status"],
            "cover_count": analysis["cover_count"],
            "expanded_nodes": analysis["expanded_nodes"],
            "equivalence_policy": PACKING_EQUIVALENCE_POLICY,
            "equivalence_policy_version": PACKING_EQUIVALENCE_VERSION,
            "normalized_signature": [list(item) for item in cover_signature(cover)],
            "normalized_signature_hash": signature_hash(cover),
        },
        "requested_band": None, "accepted_band": None, "quality_preset": None,
    }


def packing_quality_rejection(difficulty: dict, requested_band: str = "medium") -> str | None:
    preset = packing_difficulty_preset(requested_band)
    metrics = difficulty["mechanical"]
    difficulty["requested_band"] = requested_band
    difficulty["quality_preset"] = preset["name"]
    uniqueness = difficulty.get("solution_uniqueness")
    if uniqueness is not None and uniqueness["cover_count"] != 1:
        return "MULTIPLE_COVERS"
    # The neutrality perturbation is a cyclic shift of the placement order, so
    # fewer than three pieces is rejected in every band.
    if metrics["piece_count"] < max(3, preset["min_pieces"]):
        return "PIECE_COUNT_TOO_LOW"
    if metrics["piece_count"] > preset["max_pieces"]:
        return "ANIMATION_TOO_LONG"
    if metrics["tray_width_cells"] > MAX_TRAY_WIDTH_CELLS:
        return "TRAY_OVERFLOW"
    if metrics["concave_pieces"] < preset["min_concave_pieces"]:
        return "TOO_TRIVIAL"
    if metrics["dead_placements"] < preset["min_dead_placements"]:
        return "NO_FALSE_PLACEMENT"
    if metrics["difficulty_score"] < preset["min_difficulty_score"]:
        return "TOO_TRIVIAL"
    if metrics["difficulty_score"] > preset["max_difficulty_score"]:
        return "OUTSIDE_BAND"
    difficulty["accepted_band"] = requested_band
    difficulty["human"]["status"] = "uncalibrated-packing-v1"
    return None
